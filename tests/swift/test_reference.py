"""Self-consistency tests for the settlement reference tables.

These check the tables are internally well-formed — they do NOT re-assert the
SWIFT semantics (that grounding lives in the citations and the human-review gate,
epic #65 task #74). A broken table (an edge to a non-state, an uncited code, a
trigger whose code is outside its qualifier's domain) fails here.
"""

from __future__ import annotations

from gwlab.swift import reference as ref


def test_states_split_into_progressive_and_terminal_negative() -> None:
    assert ref.PROGRESSIVE[0] is ref.State.INSTRUCTED
    assert ref.PROGRESSIVE[-1] is ref.State.SETTLED
    assert ref.State.REJECTED in ref.TERMINAL_NEGATIVE
    assert ref.State.FAILED in ref.TERMINAL_NEGATIVE
    assert len(ref.TERMINAL_NEGATIVE) == 2
    # every state is either on the progressive path or terminal-negative
    assert set(ref.State) == set(ref.PROGRESSIVE) | ref.TERMINAL_NEGATIVE


def test_reachability_endpoints_are_all_states() -> None:
    for src, dsts in ref.REACHABILITY.items():
        assert isinstance(src, ref.State)
        for dst in dsts:
            assert isinstance(dst, ref.State)
            assert dst is not src  # no self-loops


def test_every_state_has_a_reachability_entry() -> None:
    assert set(ref.REACHABILITY) == set(ref.State)


def test_sinks_have_no_out_edges_and_others_do() -> None:
    for state in ref.State:
        out = ref.REACHABILITY[state]
        if state in ref.SINK:
            assert out == frozenset(), f"{state} is a sink but has out-edges"
        else:
            assert out, f"{state} is not a sink but has no out-edges"


def test_failed_can_only_recover_to_settled() -> None:
    # A failing instruction may still settle within the window (spec §4.4); it
    # must not slide back to an in-flight state.
    assert ref.REACHABILITY[ref.State.FAILED] == frozenset({ref.State.SETTLED})


def test_progression_is_forward_only_between_progressive_states() -> None:
    rank = {state: i for i, state in enumerate(ref.PROGRESSIVE)}
    for src, dsts in ref.REACHABILITY.items():
        if src not in rank:
            continue
        for dst in dsts:
            if dst in rank:
                assert rank[dst] > rank[src], f"backward edge {src} -> {dst}"


def test_instructed_reaches_acknowledged() -> None:
    assert ref.State.ACKNOWLEDGED in ref.REACHABILITY[ref.State.INSTRUCTED]


def test_status_triggers_are_valid_and_within_their_domain() -> None:
    for (qualifier, code), state in ref.STATUS_TRIGGERS.items():
        assert isinstance(state, ref.State)
        assert qualifier in ref.STATUS_DOMAINS, f"trigger qualifier {qualifier} not in domains"
        assert code in ref.STATUS_DOMAINS[qualifier], f"{qualifier}//{code} outside its domain"


def test_the_four_lifecycle_transitions_are_present() -> None:
    assert ref.STATUS_TRIGGERS[("IPRC", "PACK")] is ref.State.ACKNOWLEDGED
    assert ref.STATUS_TRIGGERS[("MTCH", "MACH")] is ref.State.MATCHED
    assert ref.STATUS_TRIGGERS[("IPRC", "REJT")] is ref.State.REJECTED
    assert ref.STATUS_TRIGGERS[("SETT", "PENF")] is ref.State.FAILED


def test_no_settled_status_code_exists() -> None:
    # SETTLED is driven by an MT544-547 arriving, never by a :25D: status.
    assert ref.State.SETTLED not in ref.STATUS_TRIGGERS.values()


def test_instruction_and_confirmation_message_types_are_disjoint() -> None:
    assert ref.INSTRUCTION_MTS.isdisjoint(ref.CONFIRMATION_MTS)
    assert ref.STATUS_MT not in ref.INSTRUCTION_MTS
    assert ref.STATUS_MT not in ref.CONFIRMATION_MTS


def test_every_status_qualifier_is_cited() -> None:
    for qualifier in ref.STATUS_DOMAINS:
        assert f"25D:{qualifier}" in ref.CITATIONS, f"uncited qualifier {qualifier}"


def test_every_status_trigger_qualifier_is_cited() -> None:
    for qualifier, _code in ref.STATUS_TRIGGERS:
        assert f"25D:{qualifier}" in ref.CITATIONS


def test_correlation_fields_are_cited() -> None:
    assert f"20C:{ref.CORRELATION_SENDER_REF}" in ref.CITATIONS
    assert f"20C:{ref.CORRELATION_RELATED_REF}" in ref.CITATIONS


def test_citation_kinds_are_from_the_allowed_set() -> None:
    allowed = {"mt-rendering", "iso20022-xsd", "worked-example", "inferred"}
    for citation in ref.CITATIONS.values():
        assert citation.kind in allowed
        assert citation.source  # non-empty


def test_field_catalog_entries_are_well_formed() -> None:
    for key, spec in ref.FIELD_CATALOG.items():
        assert spec.tag
        assert spec.fmt
        assert spec.max_len > 0
        assert spec.charset in {"x", "c", "n", "a", "d"}
        # a qualified key names its qualifier
        if ":" in key:
            assert spec.qualifier == key.split(":", 1)[1]


def test_correlation_constants_are_distinct() -> None:
    refs = {
        ref.CORRELATION_SENDER_REF,
        ref.CORRELATION_RELATED_REF,
        ref.CORRELATION_PREVIOUS_REF,
        ref.NO_REFERENCE,
    }
    assert len(refs) == 4
