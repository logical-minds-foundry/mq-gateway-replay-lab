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

### 4.5 The inbound symmetry — poison handling is state management too

The outbound argument says the **sender** owns retry state. The inbound
argument is its mirror image, and it lands on the same principle:

> The sender owns retry state. The receiver owns invalid-message state.
> **The gateway owns neither.**

A poison message is a permanent failure whose permanence depends on the message
itself. Determining that requires **parsing** the message — and the gateway is
content-opaque by design (§5.2). It therefore *cannot* make the determination,
and should not be handed the consequences of one.

The alternative design is worse than it looks. If the receiving application is
permitted to refuse a message it could not parse, the gateway must back out;
repeated backouts require a threshold; the threshold requires a backout queue;
and the backout queue requires **another component to consume it** — a second
gateway, with its own connection, its own error handling, and its own view of
what is outstanding. That is a third stateful thing in the layer this epic
argues must contain none.

So the receiving application accepts the message, records it as invalid, and
acknowledges (§5.3). Custody transfers; the lifecycle continues; no queue
backs up; no component is added.

**The strong form of the claim** — to be established by the `MQGET`
failure-mode analysis (L6, §8) rather than assumed: for a *content-opaque*
consumer, there may be **no content-dependent permanent `MQGET` failure at
all**. The two obvious candidates both dissolve on inspection: 2080 completes
with `MQCC_WARNING`, leaves the message queued and hands back the length
needed; 2110/2119 also complete with `MQCC_WARNING` and return the data
*unconverted* — and a content-opaque gateway never requests conversion, so they
cannot arise. MQ's semantics return the message rather than withholding it.

If the analysis holds, the poison-message facility is not merely unnecessary
here — it is unreachable by construction, which is a far stronger thing to be
able to say.

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

**The acknowledgement means "I have durably taken custody" — not "I processed
this successfully."** This is load-bearing, not pedantry. The receiving
application is contractually **forbidden from refusing a message it actually
received**; an unparseable message is accepted, recorded as invalid, and
acknowledged (§5.3). If the ack were allowed to mean "processed successfully",
a well-meaning implementer would nack on a parse failure and every
poison-message problem walks back in through the front door — see §4.5.

**Maximum frame size must be ≥ the queue's `MAXMSGL`**, declared and verified
rather than assumed. If the frame ceiling sat below `MAXMSGL`, messages between
the two limits would be undeliverable forever and the receiving application
would never see them — a poison class manufactured by the one layer that is
supposed to create none. In practice this is comfortable: SWIFT FIN caps
message text around 10 KB against an MQ default `MAXMSGL` of 4 MB, so this is a
declare-and-verify assertion, not defensive code.

Anything beyond that is out of scope, and its absence is the point.

Because the protocol is implemented twice — C++ in the component repo, Python
in the reference application here — a small **conformance corpus** (C4) is what
actually holds the two in agreement. Without it, "trivial but
specified" is aspiration rather than a property anything enforces, and a
framing disagreement on the ack would be indistinguishable from message loss at
exactly the moment §9 is trying to prove there is none.

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

A single-threaded blocking get-loop.

**Why single-threaded — and what that reasoning does *not* rest on.** One
consumer means no coordination between consumers, no partial failure spread
across threads, and one unit of work in flight at a time. That is the
statelessness and simplicity argument, and it stands alone. In-order delivery
is a *free consequence* of one consumer, **not the justification for it**.

This distinction is deliberate, because the stronger claim — that the
settlement lifecycle *requires* ordering — is **not currently evidenced, and is
an open question** (§11.4). It must not be asserted until it is settled.

Our own `lifecycle.py` is order-tolerant (it advances monotonically along a
reachability graph, so a late-arriving earlier status is a no-op), but that is
**not evidence about SWIFT** — it is a proof-of-concept encoding our own
assumption, and citing it to justify that assumption would be circular.

What *is* established: FIN sequences delivery at the session layer via the OSN,
assigned per delivery **attempt**; and MT548 carries field **98a PREP**, the
status's own preparation date/time. That second fact matters for the receiver
regardless of how the ordering question resolves — **the correct ordering key
is the status's own business timestamp, not arrival order and not the queue
manager's put-time.** Put-time is the transport's clock: a `PDE`
retransmission carries an old business event with a new put-time and would sort
into the wrong position. The proof-of-concept's put-time sort is wrong on that
point today.

Single-threading therefore rests on statelessness and simplicity, which stand
on their own. Whether it could safely be relaxed is a question §11.4 answers.

- Connect once; open `GW.SEC.REPLY` once; hold both for the process lifetime.
- `MQGET` under syncpoint, with a wait interval — held open in a loop, not
  polled.
- Translate and deliver downstream.
- **Content-opaque.** The gateway never parses, inspects or converts the
  message body, and never sets `MQGMO_CONVERT`. The payload is an opaque blob
  of bits moved from the queue into a frame. This is not laziness — it is what
  makes the gateway structurally incapable of creating a poison message, and it
  eliminates the `MQRC_FORMAT_ERROR` / `MQRC_NOT_CONVERTED` class outright by
  never requesting the conversion that produces it.
- **Commit the GET only on positive acknowledgement from the receiving
  application** (custody transfer, §5.0). **The wait for that acknowledgement
  must be bounded.** A receiver that dies is detected by the socket; a receiver
  that *hangs* — a half-open connection, a wedged thread — is not, and an
  unbounded wait would hold the unit of work open indefinitely. A timeout is
  treated exactly as unreachable: back out, circuit-break, retry.
- **Exactly one condition causes a backout: the receiving application is
  unreachable or does not answer.** Nothing else does. On that condition, back
  out **and halt the consumer**, reconnecting with backoff before getting
  again — an outage then costs one backout, not thousands. Backing out in a
  tight loop would spin `BackoutCount` up in seconds, and `BackoutCount` cannot
  distinguish "downstream rejected this message" from "downstream is down."
- **Handle `MQRC_TRUNCATED_MSG_FAILED` (2080) correctly**: it completes with
  `MQCC_WARNING`, leaves the message on the queue, and returns `DataLength` set
  to the untruncated length. Resize and re-get. Deterministic, one retry, no
  guessing — a programming fault if unhandled, not a poison message.
- **No `BOTHRESH`, no `BOQNAME`, no error queue** — see §4.5. Where an
  architectural assumption is nonetheless violated, **halt and alert; never
  divert.** On an ordered queue, stopping is *more* correct than skipping: a
  settlement flow that silently continues past something it could not handle is
  worse than one that stops loudly. That yields the operational signal a
  dead-letter queue would have given, without the second component and the
  state it would carry.

This path is stateless because **MQ holds the state**. The message's presence
on the queue *is* the record that it has not yet been accepted downstream.

#### The objection this design must answer head-on

> "You are holding a transaction open across a network call to a third party."

Yes. Deliberately. This is the most likely reflexive objection from a competent
reviewer, because "never hold a transaction open across I/O" is a rule most
engineers carry in their spine — and it is a good rule. It is stated here
rather than left for a reader to raise, because the answer is the strongest
proof point in the inbound design.

**The alternative is strictly worse.** Committing before delivering means the
message is destroyed while its delivery is still unproven; any downstream
failure in that window loses it, with nothing to replay from — the exact hole
this entire specification exists to close. The unit of work is not incidental
to the design, it *is* the recovery mechanism (§4.4). Shortening it to satisfy
a rule of thumb would reintroduce the defect.

**And the usual costs do not apply here.** The rule exists because open
transactions hold locks, block other work, and grow logs:

- *Contention*: none. One consumer, one queue, no competing getter to block.
- *Scale of the unit of work*: one message. `MaxUncommittedMsgs` is nowhere
  near reach.
- *Duration*: bounded by the ack timeout above, so it is a short, capped
  interval — not an open-ended hold.
- *Log pressure*: proportional to duration and volume, both small.

The honest cost is throughput: the inbound path is serial by construction, at
roughly one message per get-deliver-ack-commit cycle. §9 measures that against
expected reply volume rather than assuming it clears.

### 5.3 Reference sending application

Where state legitimately lives, built to demonstrate that placing it there is
straightforward.

**Scoping principle: the application is deliberately a black box.** This epic
demonstrates *the gateway*, not a realistic trading application. The reference
app exists to prove that the state has somewhere sensible to live and that the
contracts on it are cheap to honour — nothing more. Every temptation to make it
realistic (richer validation, business rules, a real persistence tier, an
operator interface) should be refused: it would add effort that proves nothing
about the thesis, and it would blur the boundary the epic exists to draw.

- Holds instruction state and lifecycle.
- On retry, **preserves the original SEME and sets `PDE`**.
- Consumes the inbound gateway's downstream deliveries and acknowledges only
  after durably recording them — the other half of the inbound contract.
- Must be **idempotent** on redelivery, deduplicating on SEME/RELA. This
  requirement is pushed onto it by the stateless inbound design, and is cheap
  precisely because it already holds the state.
- **Must never refuse a message on content grounds.** An unparseable or
  corrupt message is accepted, recorded durably as invalid, and acknowledged.
  Refusing it would force a backout, which forces a threshold, which forces a
  backout queue, which forces another consumer — see §4.5. Recording invalid
  messages is invalid-message *state*, and state is this component's job.

**Storage is SQLite** — embedded, single-writer, and the simplest thing that
provides a real transaction.

The one guarantee that is **not** negotiable: the record is committed inside a
transaction **before** the acknowledgement is sent. The ack means custody has
transferred (§5.0), and `MQCMIT` follows it — so an ack sent ahead of a durable
commit is a lie that MQ acts on, and the message is gone.

This is where "simple" and "weak" must be kept apart. Simple means *small in
scope*: no business rules, no operator interface, no production persistence
tier. It does not mean weak in guarantees, because this particular guarantee is
what the inbound argument rests on. Production-grade database resiliency
remains a solved problem owned elsewhere and is explicitly not instrumented
here — but atomicity at the ack boundary is instrumented, and §9 tests it.

### 5.4 Counter-example

The stateful gateway, built to their sketched design, to demonstrate
empirically that **it does not remove the failure window it was built to
remove** — the caller is still unable to distinguish acceptance from a lost
response, and the gateway has merely added a second place for the truth to
live.

Sequenced last. If the epic runs short of time this is the piece to defer, as
the argument stands without it; it converts the argument from persuasive to
demonstrated.

### 5.5 Where the code lives — two repositories

The C++ components are **not** built inside this repository. They are factored
into a new private repo, **`mq-protocol-gateway`**, built to full Vergil
standards. The reasoning is not only about tooling:

- This repo is Python and Ansible, and its pipeline is Python-shaped: `ruff`,
  `mypy`, `ty`, and `pytest --cov-branch --cov-fail-under=100` over `src/`.
  C++ sources placed here would be **invisible to every gate** — not exempted,
  simply unmeasured — silently falsifying the repo's documented zero-exemption
  coverage claim.
- Vergil ships first-class C++ support: CMake, `clang-format --dry-run
  --Werror`, `run-clang-tidy` over a compile database, `cppcheck`, an
  out-of-tree ASan/UBSan build deliberately kept from sharing object files with
  the coverage build, and a compiler-family × version matrix (`clang-…` /
  `gcc-…` tags on `[ci].versions`).
- **The standard is part of the argument.** "Here is a gateway that is
  clang-tidy clean, cppcheck clean, sanitiser clean and covered" is a stronger
  claim than correct MQI calls alone — sanitiser-clean especially, set against
  a codebase that opens objects per message.
- It follows the lab methodology: build ad hoc, find the shape, then factor the
  proven component into something separately tested and separately installable.
  A gateway that exists only inside a lab repo is a demo; a standalone tested
  component is something the team could actually adopt.

Ownership boundary:

| Repository | Holds |
|---|---|
| `mq-protocol-gateway` (new, private) | The C++ outbound and inbound gateways, the §5.0 wire protocol, the §5.4 counter-example, and their full build / test / lint / sanitiser pipeline |
| this repo | The reference sending application, the failure-envelope evidence, the API findings, the argument, the duplicate-handling reference, and the lab deployment that installs the built binaries |

Tasks in the component repo link under this epic and are closed by PRs in that
repo, satisfying the placement law. **The zero-exemption coverage invariant
then holds in both repositories** rather than being quietly abandoned in one.

The same toolchain unlocks **#228** (MQ message-exit capability), which needs
exactly this C++ build-and-test capability and is currently blocked without it.

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
| O3 | `MQPUT` interrupted mid-call — 2549 *or* 2009, see below | Error | Caller retries; same SEME, `PDE` |
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
| I3 | Message unparseable by the receiving application | None — it is accepted and acknowledged | Receiver records it as invalid (§5.3); lifecycle continues; no backout, no queue growth |
| I3b | Receiving application unreachable | Backout; consumer halts | Circuit-break with backoff; one backout per outage, not per spin |
| I4 | Acknowledged, `MQCMIT` interrupted — 2549 *or* 2009, see below | Possible redelivery | Same as I2 — receiving app deduplicates |

**Which reason code O3 and I4 actually produce depends on §7 Q1.**
`MQRC_CALL_INTERRUPTED` (2549) is documented as returned *to a client using a
**reconnectable** connection*. If Q1 resolves that the C++ classes cannot set
`MQCNO_RECONNECT`, the exemplar will instead see `MQRC_CONNECTION_BROKEN`
(2009) in these windows. **The indeterminacy is identical either way, and so is
the resolution** — retry with the SEME preserved and `PDE` set. That the
correct behaviour does not depend on which code arrives is itself worth
stating: it means the caller's obligation is robust to an API detail the caller
cannot see. The table is to be finalised once Q1 is answered empirically.

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

Labelled rather than numbered, so cross-references cannot drift as the list
changes. **C** = the component repo, **L** = this lab repo.

### In `mq-protocol-gateway` (new, private)

- **C1 — Repository bootstrap.** Vergil C++ profile, CMake, test framework,
  lint / sanitiser / coverage pipeline green on an empty skeleton. Sequenced
  first: it front-loads the toolchain risk before any gateway code exists.
- **C2 — Outbound gateway.** §5.1.
- **C3 — Inbound gateway.** §5.2 — content-opaque, circuit-breaking on an
  unreachable receiver, 2080 resize-and-re-get, and **no** poison-message
  facility (§4.5). Built with a mockable error seam so the conditions §9 marks
  *mocked* are reachable in unit tests.
- **C4 — Wire protocol.** §5.0, plus a **language-neutral conformance corpus**:
  byte-level frame fixtures and a manifest of expected parse results and acks,
  owned here and consumed as a test fixture by the Python side in the lab repo.
  Deliberately small — a normal round trip, a maximum-size frame, a zero-length
  body, the ack frame, and one split read to prove framing. Its job is to make
  a divergence between the two implementations fail a *build*, not a live run.
- **C5 — Counter-example.** §5.4; deferrable.

### In this repo

- **L1 — Reference sending application.** §5.3, SQLite-backed, with
  SEME-preserving `PDE` retry.
- **L2 — Failure-envelope document.** §6, each row carrying its §9 evidence
  tier.
- **L3 — API findings.** Q1 and Q2 answered empirically, with the XMS adoption
  note. **L2 depends on Q1** — it fixes whether O3/I4 report 2549 or 2009.
- **L4 — The argument document.** The reader-facing writeup carrying §3 and §4.
- **L5 — Duplicate-handling reference.** Extending
  `docs/swift-settlement-reference.md`, which covers none of this today.
- **L6 — `MQGET` failure-mode analysis.** A standalone reference, publishable
  on its own. Every documented `MQGET` reason code classified on **four** axes:
  - *transient vs permanent*;
  - *content-dependent vs not*;
  - **which `MQGMO` options make it reachable at all** — 2080 exists only
    without `MQGMO_ACCEPT_TRUNCATED_MSG`, 2110/2119 only with `MQGMO_CONVERT`.
    Several conditions are ones you opt into;
  - **how it can be tested** — induced, mockable-only, or neither (§9's
    tiers). Practical, and absent from every reference found.

  Only the permanent-**and**-content-dependent cell is a poison message, and
  this deliverable's verdict is whether that cell is empty for a content-opaque
  consumer (§4.5). The thesis it carries: `MQGET` is not a one-or-zero call,
  the subtlety is where the failures live, and knowing which subtleties you can
  actually rehearse is part of knowing them.
- **L7 — Lab deployment.** Install the built binaries into the lab and wire
  them to the queue managers.
- **L8 — Repo rename.** #272, sequenced late for completeness.
- **L9 — SWIFT ordering research.** #274. **Sequenced early**: it resolves
  §11.4, gates §5.2's threading decision, and determines whether the sample
  code's shared-open is a live defect or merely pointless (§5.4). Publishable
  on its own, and "no public statement found" is a legitimate result provided
  the search is documented.
- **L10 — `98C::PREP` ordering-key fix.** #275. The correlator sorts response
  streams on MQ put-time — the transport's clock — so a `PDE` retransmission
  carries an old business event with a new put-time and sorts wrongly. Wrong
  regardless of how L9 resolves, and it would **contaminate any ordering
  evidence gathered with the current collector**, so it precedes the
  ordering-sensitive validation work.

**L1, L2 and L5 require the tokenizer and generator to handle envelope-level
structure** (block 5 trailers, `23G`), which today handle block 4 only. That
work sits on the critical path of the central argument, not off to the side.

**If L6 fails** — if the analysis finds a permanent, content-dependent `MQGET`
failure a content-opaque consumer can actually hit — then §4.5 weakens and §5.2
must reinstate poison handling. That dependency is deliberate and worth stating:
the design is falsifiable by its own deliverable.

---

## 9. Validation

The argument is only worth what the live evidence behind it is worth — and
worth rather less if it overstates what was actually demonstrated.

**Every row carries a stated evidence tier.** Not all MQ conditions can be
manufactured on real infrastructure, and pretending otherwise would be the
same sin this epic criticises:

| Tier | Meaning | Examples |
|---|---|---|
| **Induced** | Produced deliberately on the live lab and observed | 2035 by revoking authority; 2009 by killing the TCP connection; 2016 by inhibiting `GET`; downstream outage by stopping the receiver |
| **Mocked** | The *handler* is proven by injecting the condition in a unit test; the condition itself cannot be produced on real infrastructure | 2549 mid-call interruption, and the codes a queue manager raises only when something has gone badly wrong internally |
| **Cited** | Neither inducible nor sensibly mockable; documented semantics only, with the source | rare internal-error paths |

This boundary is **stated, not apologised for**. A mid-call interruption cannot
be induced deterministically without interfering with the queue manager's
internals, so 2549 is provably *handled* rather than provably *observed*. Being
explicit about which is which is what makes the induced rows trustworthy.

The mocking tier is an established technique here, not new work:
`docs/coverage-exceptions-audit.md` records how the `pymqi` adapter reached
100% coverage by injecting a fake module and scripting exactly what `Queue.get`
raises. **The C++ gateway must be built with an equivalent seam** so its error
paths are reachable in unit tests — a design constraint on the exemplar, not an
afterthought, and one the gateway's structure has to accommodate from the
start.

Each failure-envelope row is then exercised at its tier and the observed
behaviour recorded — including the uncomfortable ones, such as O4.

- Induce each row; capture observed behaviour and reason codes.
- **Downstream outage that must divert nothing.** Take the receiving
  application down for longer than any plausible backout threshold. Required
  observation: the consumer halts and backs off, the queue depth grows, and
  **no message is diverted anywhere**. On recovery the flow resumes in order
  with nothing lost and nothing duplicated beyond what §6 I2 predicts.
- **Unparseable message absorbed without a backout.** Inject a corrupt payload.
  Required observation: the gateway carries it opaquely, the receiver accepts
  and records it as invalid, acknowledges, and the lifecycle continues. Queue
  depth does not grow; `BackoutCount` does not increment.
- **Oversized message.** Confirm the 2080 path resizes and re-gets, and
  separately confirm the declare-and-verify assertion that maximum frame size
  ≥ `MAXMSGL`.
- **Custody survives a receiver restart.** Kill the receiving application
  *immediately after it acknowledges* and before anything else happens; restart
  it. Required observation: the record is present and the lifecycle continues.
  This is the only scenario that distinguishes a genuine durable commit from an
  in-memory one, and without it an unsound implementation passes every other
  test here.
- **Inbound throughput ceiling.** The inbound path is serial by construction
  (§5.2): one message per get-deliver-ack-commit cycle, with the network
  round-trip inside the unit of work. Measure that rate and state it against
  the expected reply volume, with the margin shown. A confirmation flow is
  almost certainly nowhere near the ceiling — but "almost certainly" is exactly
  the kind of claim this document refuses everywhere else, and an unmeasured
  serial bottleneck is a free objection handed to a reviewer. The repo's
  existing `scale_bench.py` and `docs/scale-analysis.md` are the precedent.
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
- **Quality standards are part of the deliverable, not overhead.** The C++
  component is held to full Vergil standards — format, clang-tidy, cppcheck,
  ASan/UBSan and coverage — because an epic whose thesis is that the other
  design lacks engineering rigour cannot ship an unmeasured exemplar. The
  zero-exemption coverage invariant holds in both repositories (§5.5).

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
4. **Message ordering — open, and load-bearing.** Whether the settlement
   lifecycle genuinely requires in-order processing is **unresolved**, and it
   gates the single-threaded inbound decision (§5.2). It must be settled from
   primary sources, not from our own proof-of-concept.

   *Established:* FIN sequences delivery at the session layer — the OSN is
   assigned per delivery **attempt** on the receiver's output session, so gaps
   and retransmissions are detectable. MT548 carries **98a PREP**, the status's
   own preparation date/time, which is the correct ordering key for a receiver.

   *Unresolved:* whether SWIFT states an end-to-end in-sequence delivery
   guarantee; and what the official expectation is for an application that
   receives a status advice before its predecessor — is that an error to flag,
   or an ordinary condition to absorb? Sources to pursue: the SWIFT Knowledge
   Centre FIN service description and System Messages guides, and SMPG market
   practice for MT548/MT537 (including its Pending Transaction Reporting
   guidance).

   *Why it matters beyond our design:* the sample C++ opens the reply queue
   with `MQOO_INPUT_SHARED`, permitting a second consumer. Multiple consumers
   **race** — priority governs queue position, not which consumer wins — so
   that code destroys ordering by construction while apparently assuming
   ordering holds. If ordering does not matter, the shared open is merely
   pointless; if it does, it is a live defect. Either way the answer belongs in
   the argument, and the shared open is a named anti-pattern for §5.4.

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
