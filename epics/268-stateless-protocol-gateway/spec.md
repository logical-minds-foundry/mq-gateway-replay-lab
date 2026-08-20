# Epic #268 — stateless MQ protocol gateway — spec

*A generic trading back-office gateway problem. No employer/client/vendor
identifiable content; invented queue/host/identity names only.*

---

## 1. Origin — why this epic exists

The application team wants its applications to stop linking against, and
knowing about, the MQ client libraries. Their answer is a gateway: applications
speak some ordinary non-MQ protocol to it, and it speaks MQ on their behalf.
That factoring is sound and this epic does not dispute it.

What this epic disputes is the **shape** they have given that gateway.

**What was shared (data).** A C++ source excerpt covering the put and get
paths. Two properties are visible in it directly:

- Objects are opened **per message** rather than once at startup.
- Puts and gets are performed **outside syncpoint** — no unit of work is begun,
  committed or backed out anywhere in the excerpt.

**What was stated (data).** The gateway is intended to be **stateful**: it
records what it has sent, and after a crash it replays the messages it believes
were not confirmed. When asked how that record would survive the gateway host,
the answer given was to write it to a filesystem on another machine and run the
recovery there. No HA/DR is planned for the gateway tier itself.

**What follows (judgment).** The per-message open is a straightforward
efficiency defect. The missing syncpoint on the get path is a correctness
defect that loses messages. But the replay design is the important one, because
it is *architectural* — it cannot be fixed by better MQI calls, and it is the
part the team is most committed to.

This epic exists to argue that the replay design is wrong, and to back the
argument with a working exemplar measured against a live HA/DR queue manager
rather than with assertion.

---

## 2. Mission

Produce the evidence and the argument that a protocol gateway fronting MQ
**must be stateless in both directions**, together with a working exemplar
that demonstrates correct MQI usage within the API the team already uses.

The exemplar is built by **factoring the MQ out of the application we already
have**. The thread that called `MQGET` becomes a thread listening on a port.
The code that called `MQPUT` becomes an RPC. Nothing else about the
application changes — and in particular, **its state handling does not move**.
That construction is the argument in miniature: the factoring is a protocol
boundary, and a protocol boundary is not a place where state appears.

The deliverable is a **recommendation with proof**, not a product. Success is
that a reader who arrived believing the gateway should replay leaves
understanding why it must not, and holding code that shows the alternative.

---

## 3. The thesis

> The gateway is a **protocol layer factored out of an already-stateful
> application**. Making the protocol layer stateful is a fatal flaw: it sits
> between two components that are *already* stateful — the application, which
> owns the business state, and the queue manager, which owns the message state
> — and needs none of its own.

The team has not invented a new component that needs state. They have taken an
existing stateful application and factored out its MQ protocol handling. The
state stayed where it was. Re-introducing it in the extracted layer creates a
second, divergent copy of something that already exists on both sides of it.

Everything below is an elaboration of that one sentence.

---

## 4. The four arguments

Each is to be evidenced in the deliverables, not merely asserted.

### 4.1 Statefulness duplicates the indeterminate window; it does not remove it

The motivation for gateway replay is the fear of a message whose fate is
unknown. That fear is well-founded — the window is real, and IBM names it:
`MQRC_CALL_INTERRUPTED` (2549) is returned when `MQPUT`, `MQPUT1` or `MQCMIT`
is interrupted and reconnection processing cannot re-establish a definite
outcome.

But the window is a property of **any non-transactional request/response
boundary**, and the call *into* the gateway is exactly such a boundary. However
durably the gateway records intent, the caller still cannot distinguish "the
gateway accepted it" from "my response was lost." The caller therefore still
needs its own retry and duplicate-marking logic — precisely the logic it would
have needed had the gateway been stateless.

**So gateway state buys the caller nothing it can stop doing.** It adds a
second source of truth, which can diverge from the first, and a new component
whose loss destroys information.

Indeterminacy cannot be pushed downstream. It can only be *assigned* — and it
must be assigned to the party holding the business identity of the message.

### 4.2 The message standard already places the duplicate marker at the sender

This is the argument that removes the matter from the realm of architectural
preference. Three independent layers of the standard agree, and all three place
the marker at the **sender**, keyed to a reference the **sender** mints and
retains:

| Layer | Mechanism | Held by |
|---|---|---|
| FIN network (block 5) | `PDE` trailer, `{PDE:[<time><MIR>]}` | Sender, referencing its own prior emission's MIR |
| ISO 15022 business level | `:20C::SEME//`, unique by instructing party; `23G` function | Sender |
| ISO 20022 (BAH) | `CpyDplct` (`CODU`/`COPY`/`DUPL`), `PssblDplct`, mandatory `Rltd` back-reference | Sender |

Note the contrast with `PDM`, which *is* system-added — the network marks its
own redeliveries with `{PDM:[<time><MOR>]}`. The standard clearly distinguishes
"the transport is redelivering" from "the sender may be retransmitting", and
only the latter is the sender's to assert.

A protocol gateway mints none of these. It does not allocate the SEME. It holds
no MIR from a prior attempt. It cannot know whether a given inbound call is a
first attempt or a retry unless told — and a caller able to tell it already
holds the state that would make the gateway's copy redundant.

**Conformance to the message standard therefore requires the state to be
application-side.** This is not our design preference competing with theirs; it
is what the protocol they are carrying was designed to assume.

### 4.3 A stateful gateway converts a retryable outage into an unrecoverable one

- **Stateless.** A gateway crash is an **availability** failure. The caller
  receives an error, retries with the SEME preserved and `PDE` set, and
  proceeds. Recoverable by construction, and recoverable *without* the gateway.
- **Stateful.** A gateway crash is a **data** failure. It dies holding the only
  record of outcomes that no other party knows. Recovery requires that record
  to have survived, on infrastructure that is not planned to be highly
  available.

The stated recovery plan — copy a file to another machine and run the recovery
code there — is not a design; it is the absence of one. It leaves unanswered
what is written and when, how the copy is made durable, what the copy's
consistency guarantee is relative to the MQ commit, and what happens if the
gateway dies between the commit and the write.

**The design adds a critical stateful tier and then declines to protect it.**
That is the sentence to lead with.

### 4.4 Syncpoint is a coordination mechanism, and the two directions differ

Syncpoint is not a general-purpose safety feature to be applied uniformly. It
coordinates *multiple* operations into one outcome. Where there is nothing to
coordinate, it does not buy safety — it buys a network round trip and relocates
the uncertainty.

- **Outbound.** One put, no second resource, no state to update. Under
  syncpoint an interrupted `MQPUT` backs the unit of work out — definitively no
  message — and the uncertainty concentrates entirely at `MQCMIT`. Without
  syncpoint, `MQPUT` itself is the uncertain call. **One indeterminate point
  either way**, and the syncpoint version costs an extra round trip.
  *(Judgment: their omission of syncpoint here is defensible, possibly
  accidentally correct. The spec deliberately concedes this.)*
- **Inbound.** The GET is **destructive**, and it must be coordinated with
  downstream acceptance. Here syncpoint is not an optimisation — it **is** the
  entire recovery mechanism. Getting outside syncpoint means a message consumed
  and then lost to any downstream failure, with nothing to replay from.

Conceding the outbound point is what makes the inbound critique credible.
"Wrong about both" invites dismissal as pedantry; "wrong about one, and that
one is losing you messages" does not.

---

## 5. Architecture

Two **separate processes**, deliberately not one. They have different
concurrency models, different scaling characteristics and different failure
domains; separating them also makes the statelessness self-evident rather than
a property that must be argued, and allows either direction to be killed
independently during failover testing.

### 5.0 The application-facing transport — deliberately trivial

The protocol between the applications and the gateway is **arbitrary and
unknown**. It will not be exposed to us, we do not need it, and none of the
argument depends on it.

Naming a specific technology is therefore an active risk, not a convenience.
"We don't use that" is a cheap way to dismiss the whole design without engaging
with any of it — and the dismissal would be unanswerable, because the choice
genuinely is irrelevant. Worse, naming one would make a **transport-independent
argument look transport-specific**: every window in §6 arises from *any*
non-transactional request/response boundary, which is precisely the claim §4.1
rests on.

The exemplar therefore uses a **hand-rolled, minimal TCP protocol** —
deliberately proprietary and deliberately uninteresting, so that no reader
mistakes it for a recommendation or an assumption about their stack.

It must nonetheless be **specified**, because the inbound contract depends on
it. Trivial is not the same as vague:

- Length-prefixed frames; no negotiation, no versioning, no framing subtleties.
- Request → response carrying an explicit status, so the caller can
  distinguish retryable from terminal outcomes.
- Inbound: an explicit **positive acknowledgement**, because that
  acknowledgement is what gates the `MQCMIT` in §5.2. If the ack semantics are
  mushy, the inbound argument is mushy.

Anything beyond that is out of scope, and its absence is the point.

### 5.1 Outbound gateway

A protocol translator. One request in over the §5.0 transport, one put, one
deterministic outcome returned.

- Connect once; open `GW.SEC.INSTRUCT` once; hold both for the process
  lifetime. Never per message.
- One put per request.
- Return a deterministic success/failure to the caller, with the MQ reason code
  surfaced so the caller can distinguish retryable from terminal conditions.
- **No state. No replay. No custody of the caller's message.**

The gateway's contract, stated for the caller:

> This gateway carries your message to the MQ boundary and tells you truthfully
> whether it crossed. It never takes ownership of your message. If it cannot
> tell you, assume it did not cross, retry with the same SEME, and mark `PDE`.

### 5.2 Inbound gateway

A single-threaded blocking get-loop. The reply queue is ordered and
concurrency here would destroy the ordering the settlement lifecycle depends
on.

- Connect once; open `GW.SEC.REPLY` once; hold both for the process lifetime.
- `MQGET` under syncpoint, with a wait interval — held open in a loop, not
  polled.
- Translate and deliver downstream.
- **Commit the GET only on positive acknowledgement from the receiving
  application.** Back out on any failure, and the message is still there.
- **Poison-message handling via `BOTHRESH` / `BOQNAME`.** A message that can
  never be processed will otherwise be re-got forever; on an *ordered* queue
  that is head-of-line blocking, and the settlement flow stops outright. After
  the backout threshold the message is requeued to the backout queue and the
  loop proceeds. This is a deliberate, visible break in ordering and **must**
  alert.

This path is stateless because **MQ holds the state**. The message's presence
on the queue *is* the record that it has not yet been accepted downstream.

### 5.3 Reference sending application

Where state legitimately lives, built to demonstrate that placing it there is
straightforward.

- Holds instruction state and lifecycle.
- On retry, **preserves the original SEME and sets `PDE`**.
- Consumes the inbound gateway's downstream deliveries and acknowledges only
  after durably recording them — the other half of the inbound contract.
- Must be **idempotent** on redelivery, deduplicating on SEME/RELA. This
  requirement is pushed onto it by the stateless inbound design, and is cheap
  precisely because it already holds the state.

Storage is deliberately simple and embedded. Production-grade database
resiliency is a solved problem owned elsewhere and is explicitly not
instrumented here.

### 5.4 Counter-example

The stateful gateway, built to their sketched design, to demonstrate
empirically that **it does not remove the failure window it was built to
remove** — the caller is still unable to distinguish acceptance from a lost
response, and the gateway has merely added a second place for the truth to
live.

Sequenced last. If the epic runs short of time this is the piece to defer, as
the argument stands without it; it converts the argument from persuasive to
demonstrated.

---

## 6. The failure envelope

The enumerated windows, and who resolves each. This table is a primary
deliverable in its own right — it is the artefact that answers "how do we know
if we sent the message?"

**These windows are transport-independent.** Not one of them is a property of
the application-facing protocol; they arise from the *shape* of a
non-transactional request/response boundary, whatever carries it. Substituting
any other transport changes nothing in either table. This is what makes §5.0's
refusal to name a technology a strengthening of the argument rather than a
hand-wave.

### 6.1 Outbound

| # | Window | Caller sees | Resolved by |
|---|---|---|---|
| O1 | Request lost before reaching the gateway | Timeout | Caller retries; same SEME, `PDE` |
| O2 | Gateway dies before `MQPUT` | Timeout / connection reset | Caller retries; same SEME, `PDE` |
| O3 | `MQPUT` interrupted — 2549 | Error | Caller retries; same SEME, `PDE` |
| O4 | Put succeeded, response to caller lost | Timeout | Caller retries; same SEME, `PDE` |

Every outbound row resolves the same way, and none of them involve the
gateway. That uniformity **is** the argument: there is no window whose
resolution requires gateway state.

O4 is the irreducible case — the caller is told "failed" for a message that
went. No design without XA removes it, and XA is unavailable on a client
connection. The correct response is not to hide it but to make the retry safe,
which `PDE` does.

### 6.2 Inbound

| # | Window | Effect | Resolved by |
|---|---|---|---|
| I1 | Gateway dies after GET, before downstream delivery | Uncommitted GET backs out | Automatic — message still queued |
| I2 | Delivered, acknowledgement lost, GET backed out | Duplicate delivery downstream | Receiving app deduplicates on SEME/RELA |
| I3 | Downstream rejects; message unprocessable | Repeated backout | `BOTHRESH` → `BOQNAME`; alert; ordering break visible |
| I4 | Acknowledged, `MQCMIT` interrupted — 2549 | Possible redelivery | Same as I2 — receiving app deduplicates |

Inbound recovery requires no gateway state at all: MQ's own transactionality
provides it, provided the gateway is written to use it correctly.

---

## 7. Language and API

**C++, using the IBM MQ C++ classes (`imqi.hpp`)** — the baseline the team
already uses. The objective is to show the correct architecture *within* the
API they have. Making the recommendation contingent on an API migration would
give them an easy reason to reject all of it.

Two questions to settle with evidence:

**Q1 — Can the C++ classes enable automatic client reconnection?** The
documented `ImqQueueManager::setConnectOptions` option list comprises binding
type, handle-sharing and connection-tag serialisation; `MQCNO_RECONNECT` and
`MQCNO_RECONNECT_Q_MGR` are absent (data, from a 9.0-era reference — version
ambiguity acknowledged). **To be settled empirically** against the `imqi.hpp`
shipped on the lab box and by attempting the connect. An empirical answer is
worth more here than a citation.

**Q2 — What would automatic reconnection actually have bought?** IBM documents
that a connection enlisted in a transaction delays reconnection until the
in-flight transaction completes, and that transactions are rolled back; 2549
exists because reconnection cannot always establish a definite outcome.

*Working hypothesis (judgment, to be tested): reconnection re-establishes a
connection, never a unit of work.* If it holds, the recovery logic is mandatory
regardless of API, the C++ classes cost boilerplate rather than correctness,
and Q1's answer stops being load-bearing — a substantial de-risking of the
whole API question.

**On XMS for C/C++.** It is delivered as SupportPac IA94 — a separate download
rather than a base-product component (data). That is a materially different
adoption story from "use the newer API", and must be reported as such. The
`imqi.hpp` classes' own stabilisation status could **not** be confirmed from
IBM documentation; the widely-cited stabilisation statement is for the *Java*
classes. This spec does not assert it of C++.

---

## 8. Deliverables

1. **Outbound gateway** (C++) — §5.1.
2. **Inbound gateway** (C++) — §5.2, including poison-message handling.
3. **Reference sending application** — §5.3, with SEME-preserving `PDE` retry.
4. **Failure-envelope document** — §6, with each window demonstrated live.
5. **API findings** — Q1 and Q2 answered empirically, with the XMS adoption
   note.
6. **The argument document** — the reader-facing writeup carrying §3 and §4.
7. **Duplicate-handling reference** — extending
   `docs/swift-settlement-reference.md`, which covers none of this today.
8. **Counter-example** (§5.4) — deferrable.

Deliverables 3, 4 and 7 require the tokenizer and generator to handle
**envelope-level structure** (block 5 trailers, `23G`), which today handle
block 4 only. That work is on the critical path of the central argument, not a
nicety.

---

## 9. Validation

The argument is only worth what the live evidence behind it is worth. Each
failure-envelope row is to be induced against the running 3+3 HA/DR lab and its
observed behaviour recorded — including the ones that are uncomfortable, such
as O4.

- Induce each row; capture observed behaviour and reason codes.
- Prove I3 end to end: a poison message on an ordered queue, head-of-line
  blocking observed, `BOTHRESH` releasing it, the alert firing.
- Exercise both gateways across an HA failover and a DR event.
- Measure, do not assume, anything presented as a performance property.

---

## 10. Constraints and doctrine

- **Anonymisation is absolute.** No employer/client-identifiable content
  anywhere. The counter-example is a *generic anti-pattern exemplar*, never a
  reproduction of shared source.
- **Self-owning bolt-on rule.** Any change to the underlying lab is automated,
  owned, replayable from scratch, and stays generic in the base lab.
- **Reuse none of the lab's tooling.** This repo ships its own CLI and apps.
- **XA is out — permanently.** `MQBEGIN` is not valid on a client connection,
  and the gateway will always connect as a client. This is an invariant of the
  target environment, not a deferred option.
- **Out of scope:** authorization, payload validation and business rules;
  HA/DR of any state store; re-implementing the team's application.
- **The "MQ-native & minimalist / no new vendor products" rule is not
  breached.** The reference application's embedded store demonstrates where
  state belongs; it is not new lab infrastructure. No database node is added.

---

## 11. Stated assumptions and open questions

Load-bearing, and originating outside this repo. Recorded rather than silently
assumed.

1. **Duplicate tolerance.** The argument rests on an **asymmetry**: a duplicate
   is detectable, markable and recoverable through a standardised path; a lost
   message is none of those. Note that `PDE`-marked messages are commonly
   routed to *exception handling* rather than silently discarded — duplicates
   cost operational attention. The claim to make is the asymmetry, not
   harmlessness. **To be confirmed as the application team's position.**
2. **The return path.** How the receiving side is intended to consume replies
   has not been shared. This epic models it as it should be built and presents
   that as the recommendation. Asking the question is itself diagnostic: if
   something already tracks the reply lifecycle, gateway state is redundant; if
   nothing does, they cannot answer what they are missing a confirmation for,
   which is a larger problem than the put path.
3. **UETR.** Field 121 was mandated by SR2018 for the payments family. **No
   evidence was found** that it applies to category 5 securities messages, and
   this spec does not rely on it.

---

## 12. Sources

Re-read directly before relying on any of these operationally.

- `MQRC_CALL_INTERRUPTED` (2549) —
  <https://www.ibm.com/docs/en/SSFKSJ_latest/reference/q045480_.html>
- `MQRC_BACKED_OUT` (2003) —
  <https://www.ibm.com/docs/en/SSFKSJ_latest/reference/q040770_.html>
- Automatic client reconnection —
  <https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=restart-automatic-client-reconnection>
- Global units of work (`MQBEGIN`, client restriction) —
  <https://www.ibm.com/docs/SSFKSJ_9.3.0/develop/q026880_.html>
- `ImqQueueManager` C++ class —
  <https://setgetweb.com/p/MQ90/ref.dev/q111750_.htm>
- IA94: XMS for C/C++ —
  <https://www.ibm.com/support/pages/ia94-ibm-message-service-client-xms-cc>
- PDE trailer —
  <https://developer.swift.com/glossary/possible-duplicate-emission-trailer-pde-trailer>
- Block 5 trailers —
  <https://www.paiementor.com/swift-mt-message-block-5-trailers-description/>
- System trailers (PDM/MOR) —
  <https://learn.microsoft.com/en-us/biztalk/adapters-and-accelerators/accelerator-swift/system-trailers>
- MT 543 field 20C (SEME uniqueness) —
  <https://www.iso20022.org/15022/uhb/mt543-2-field-20c.htm>
- ISO 20022 BAH FAQ (`CpyDplct`) —
  <https://www.iso20022.org/sites/default/files/2020-10/BAH_FAQ_v11.pdf>
- SR2018 UETR mandate (payments only) —
  <https://www.swift.com/news-events/news/standard-mt-release-2018-mandatory-changes-category-1-and-category-2>
