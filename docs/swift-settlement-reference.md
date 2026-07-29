# SWIFT MT540–548 settlement reference (grounded, cited)

**Epic:** #65 · **Task:** #74 (reference-grounding spike, GATING)
**Status:** draft for human review — the reachability graph and the decisions in
§7 must be signed off before Phase 1 (parser / state-machine) code begins.

This is the cited source of truth behind `src/gwlab/swift/reference.py`. Every
status code, qualifier, and field format below traces to a public source, listed
inline. Where a free public source did not yield a value we mark it and apply the
**source-fallback policy** (§8) rather than guess. Copyrighted SWIFT MT Reference
Guide prose is not reproduced; structure is taken from public renderings.

## 1. The message family (ISO 15022 Category 5, Securities)

| MT | Role | Direction (account-owner viewpoint) |
|----|------|-------------------------------------|
| 540 | Receive Free — instruction | outbound |
| 541 | Receive Against Payment — instruction | outbound |
| 542 | Deliver Free — instruction | outbound |
| 543 | Deliver Against Payment — instruction | outbound |
| 544 | Receive Free — **confirmation** | inbound |
| 545 | Receive Against Payment — **confirmation** | inbound |
| 546 | Deliver Free — **confirmation** | inbound |
| 547 | Deliver Against Payment — **confirmation** | inbound |
| 548 | Settlement **Status / Processing Advice** | inbound |

Sources: the ISO 15022 UHB page titles (e.g. `https://www.iso20022.org/15022/uhb/finmt541.htm`
… `finmt548.htm`) and the iotafinance renderings (e.g.
`https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT541.html`). The
540/542→544/546 and 541/543→545/547 instruction→confirmation pairing is our
synthesis of the eight page titles (labelled inference).

We model the **account-owner** viewpoint: we send 540–543; the account
servicer/CSD returns 548 status advices and 544–547 confirmations.

## 2. Message structure and the envelope

A SWIFT message is framed `{1:<basic header>}{2:<application header>}{4:<body>-}`.
The **MT number is in Block 2**, not the body — e.g. an input instruction
`{2:I541<dest-BIC12><priority>…}`, an output advice `{2:O548<time><MIR>…}`
(format: paiementor,
`https://www.paiementor.com/swift-mt-message-block-2-application-header-description/`).
No free source published a full `{1:}{2:}{4:}` wrapper around an MT5xx; the
envelope *format* plus non-54x concrete examples are public, which is enough to
render one. The parser therefore reads the MT from Block 2 and hands Block 4 to
the field tokenizer.

Block 4 is a sequence of `:16R:<BLOCK>` … `:16S:<BLOCK>` sequences containing
`:<tag>:<value>` fields; a reference/status field is
`:<tag>::<qualifier>//<value>` (or `:<tag>::<qualifier>/<DSS>/<value>`).

**Sequences (MT541/543 instruction):** GENL (A, mandatory) · LINK (A1, optional,
repetitive) · TRADDET (B, mandatory) · FIA (B1) · FIAC (C, mandatory) · SETDET
(E, mandatory, with nested SETPRTY and AMT). **MT548** uses GENL · LINK · STAT
(`:25D:` status) · REAS (`:24B:` reason) · SETTRAN. Source: SWIFT ISO 15022
manual mirror `https://pinas.synology.me/Swift_Manual/2015/books/us5mb/alif009.htm`
and worked examples (§6).

## 3. Correlation — how a response links to its instruction

- The instruction carries its own **`:20C::SEME//`** (Sender's Message
  Reference) in GENL — mandatory. Format `:4!c//16x`.
- A response (548, 544–547) carries **`:20C::RELA//`** (Related Reference) in a
  LINK sub-sequence, whose value **is the instruction's `SEME`** (`NONREF` when
  none). `:20C::PREV//` links a cancellation/second-leg to a previously *sent*
  message.

The `RELA`←`SEME` echo is documented: an ASX market notice describes "changing
the `20C::RELA/` reference to reflect the `20C::SEME/` reference"
(`https://asxonline.com/public/notices/2021/June/0794.21.06.html`), and the worked
OeKB MT548 (§6) carries `:20C::PREV//272883` linking the MT543 whose
`:20C::SEME//272883` it reports on. We follow the reference echo (`RELA`, with
`PREV` for cancellation chains — §7); we do **not** do economic matching.

## 4. Status qualifiers and codes (MT548 `:25D:`)

Format `:4!c/[8c]/4!c` = (qualifier)(optional Data Source Scheme)(status code).
Codes below apply when no DSS is present. Source: iotafinance MT548
(`https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html`),
**cross-checked against the ISO 20022 `sese.024` XSD** status structure
(`ProcessingStatus`/`MatchingStatus`/`SettlementStatus` choices;
`https://raw.githubusercontent.com/mgruener/iso20022tpl/HEAD/assets/xsd/sese.024.001.10.xsd`),
which agrees.

| Qualifier | Meaning | Documented codes |
|-----------|---------|------------------|
| `IPRC` | Instruction Processing Status | CAND, CANP, CGEN, CPRC, MPRC, **PACK**, PPRC, **REJT**, REPR |
| `MTCH` (`INMH`) | Matching Status | **MACH**, NMAT |
| `SETT` | Settlement Status | PEND, **PENF** |
| `CPRC` | Cancellation Processing Status | CAND, CANP, DEND, PACK, REJT |
| `SPRC` | Statement/Status-request Status | REJT |
| `CALL` | Repo Call Request Status | CACK, DEND |
| `TPRC` | Processing-change Command Status | DEND, MODC, MOPN, PACK, REJT |

Key meanings (verbatim from the rendering): `PACK` = **Acknowledged/Accepted**;
`REJT` = **Rejected** for further processing; `MACH` = **Matched**, `NMAT` =
Unmatched; `PEND` = Pending settlement (still possible), `PENF` = **Pending/
Failing** (settlement at the instructed date no longer possible).

**Reason `:24B:`** (same format) carries qualifiers REJT/NMAT/PEND/PENF/CAND/…
with large code lists; the settlement-hold reasons include `LACK` (lack of
securities) and `MONY` (lack of money) under PEND/PENF.

## 5. The state machine

Six states; four progressive, two terminal-negative:

```
INSTRUCTED ─▶ ACKNOWLEDGED ─▶ MATCHED ─▶ SETTLED        (progressive)
     │             │             │
     └─────────────┴─────────────┴─▶ REJECTED / FAILED   (terminal-negative)
                                        FAILED ─▶ SETTLED (recovery within window)
```

**State-advancing triggers** (only these move the machine):

| Trigger | → State | Source |
|---------|---------|--------|
| MT548 `:25D::IPRC//PACK` | ACKNOWLEDGED | §4; live in OeKB MT548 (§6) |
| MT548 `:25D::MTCH//MACH` | MATCHED | §4 |
| MT544 / 545 / 546 / 547 arrives | SETTLED | §1 (no `:25D:` "settled" code exists) |
| MT548 `:25D::IPRC//REJT` | REJECTED | §4 |
| MT548 `:25D::SETT//PENF` | FAILED | §4 |

**Recognised-but-non-advancing:** every other documented `:25D:` code (e.g.
`SETT//PEND`, `IPRC//PPRC`, `IPRC//REPR`) is valid — so never an anomaly — but
does not change state. See §7 for the ones worth reconsidering.

**Reachability** (`reference.REACHABILITY`) is forward-only among the progressive
states, with the terminal-negatives reachable from the in-flight states and a
single recovery edge FAILED→SETTLED. It is intentionally permissive on skips
(e.g. INSTRUCTED→MATCHED) so the fold tolerates missing or late advices. REJECTED
and SETTLED are hard sinks.

## 6. Worked examples located (public, © their publishers)

Full block-4 messages retrieved (structure calibration only — **not** to be
republished verbatim, and their institution-flavoured data must **not** be copied
into our corpus per the anonymization rule):

- **MT541 ×2** — Euronext, `…/4516 - esempi MT541 e MT543_0.pdf`.
- **MT543, MT548, MT544** — OeKB CSD, `Using-T2S-for-Transaction-Management-SWIFT-ISO15022-Examples.pdf`
  (`https://www.oekb-csd.at/…`). The MT548 there shows `:23G:INST`, LINK blocks
  (`RELA//NONREF`, `PREV//272883`, `PCTI`, `MITI`, `CORP`), a `:25D::IPRC//PACK`
  STAT block, and a SETTRAN sequence with `:22H::REDE//DELI` / `:22H::PAYM//APMT`.

**Illustrative, fully anonymised** message (invented data — our own Counterparty
A/B convention, not any source's values):

```
{1:F01GWLABXXA0XXX0000000000}{2:I541CPTYAAA0XXXN}{4:
:16R:GENL
:20C::SEME//GWSEC0000000001
:23G:NEWM
:16S:GENL
:16R:TRADDET
:98A::TRAD//20260727
:98A::SETT//20260729
:35B:ISIN XX0000000001
GENERIC EQUITY NPV
:16S:TRADDET
:16R:FIAC
:36B::SETT//UNIT/1000,
:97A::SAFE//GWSAFE0001
:16S:FIAC
:16R:SETDET
:22F::SETR//TRAD
:16R:SETPRTY
:95P::PSET//CPTYBBB0XXX
:16S:SETPRTY
:16S:SETDET
-}
```

## 7. Decisions for human review (the gate)

These are the judgement calls the spike surfaces; each is encoded one way now but
is exactly what to confirm before Phase 1:

1. **Reachability edges.** We allow forward skips (missing-advice tolerance) and
   exclude MATCHED→REJECTED (rejection is an instruction-processing event, not a
   post-match one). Confirm the edge set in `REACHABILITY`.
2. **FAILED→SETTLED recovery.** `PENF` is "pending/failing" (still pending), and
   spec §4.4 tracks a failing instruction until it settles or ages out — so we
   allow FAILED→SETTLED. Confirm FAILED is *not* a hard sink.
3. **Recognised-but-non-advancing codes.** Should any of `IPRC//REPR` (accepted,
   in repair), `IPRC//PPRC` (pending processing), or `IPRC//CAND`/`CANP`
   (cancelled/cancelling) advance or fork the machine? Today they are valid but
   inert; cancellation is out of the six-state model.
4. **`:20C::PREV//` chains.** Confirmed present for cancellation/second-leg links.
   Task 5 handles it only if you want amendment/cancel tracking; default is to
   record `PREV` and not chase the chain.

## 8. Source-fallback policy and gaps

Order applied for every value: a **cited free MT rendering** → else the **free
ISO 20022 code list / `sese.024` XSD** with the 15022↔20022 mapping recorded as a
**labelled inference** → else **mark unsupported and omit** (never guess).

Labelled inferences (not primary-source statements): the instruction→confirmation
MT pairing (§1); the fails-reason mapping ISO 15022 `LACK`/`MONY` ↔ ISO 20022
`CLAC`/`CMON`; the `INMH` value set (assumed equal to `MTCH`).

Gaps / NOT FOUND (flagged, not filled): the authoritative `iso20022.org` UHB was
unreachable during research (values single-sourced from iotafinance then
cross-checked against the free `sese.024` XSD); no OSS library ships raw MT54x
sample fixtures (Prowide, Apache-2.0, and `swift-parser`, MIT, both lack them,
though `swift-parser`'s `metadata/patterns.json` gave the field formats); no
pre-assembled `{1:}{2:}{4:}` MT5xx envelope in a free source; MT540/542/545/
546/547 full bodies not individually retrieved (structure via Prowide javadoc +
the retrieved MT541/543/544/548).

## 9. Sources

- iotafinance MT548 rendering — `https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html`
- SWIFT ISO 15022 manual mirror (field 20C) — `https://pinas.synology.me/Swift_Manual/2015/books/us5mb/alif009.htm`
- ISO 20022 `sese.024` XSD (free mirror) — `https://raw.githubusercontent.com/mgruener/iso20022tpl/HEAD/assets/xsd/sese.024.001.10.xsd`
- `swift-parser` field patterns (MIT) — `https://raw.githubusercontent.com/pstodulka/swift-parser/master/metadata/patterns.json`
- Prowide Core (Apache-2.0) — `https://github.com/prowide/prowide-core`
- OeKB CSD worked examples — `https://www.oekb-csd.at/dam/jcr:586d6dbf-109c-4261-aac7-e4ccca017e0b/Using-T2S-for-Transaction-Management-SWIFT-ISO15022-Examples.pdf`
- Euronext MT541/543 examples — `https://www.euronext.com/sites/default/files/2022-09/4516%20-%20esempi%20MT541%20e%20MT543_0.pdf`
- ASX RELA/SEME notice — `https://asxonline.com/public/notices/2021/June/0794.21.06.html`
- SWIFT block-2 envelope format — `https://www.paiementor.com/swift-mt-message-block-2-application-header-description/`
