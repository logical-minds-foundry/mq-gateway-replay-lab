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

## 4. The arguments

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

### 4.6 Complexity should be proportional to measured risk

HA and DR are risk-management investments. Individual components have
unremarkable mean times to failure; combined, they make the *service's* mean
time to failure very long, and any residual failure short and recoverable. How
much to invest is a cost-benefit question with an arithmetic answer.

That arithmetic is almost never done. It is asserted, and the assertions tend
to be shaped by instinct rather than exposure.

**The sharpest form of the critique is not that their gateway is over-built.
It is that it is over-built *and* under-engineered at the same time.** It adds
a stateful replay tier — real complexity, real new failure modes, a new thing
requiring its own HA — while omitting syncpoint on the get path, which silently
loses messages. Complexity was spent generously; it was simply spent somewhere
uncorrelated with where the risk actually is. The problem is not the quantity
of engineering but its *distribution*.

The corresponding claim for this design is falsifiable, which is the point:
making the gateway stateless, placing retry with the sender and duplicate
handling with the receiver, removes a tier **without** removing availability
(§5.6) — and if the numbers said otherwise, we would want to know.

**What this spec can and cannot supply.** We do not have their message rates,
host MTBFs, deployment topology, or the business cost of a lost settlement
instruction, and we will not get them. A document asserting failure frequencies
would be the same hand-waving in better clothes. What we *can* build is the
**model**: the expected-failure-frequency relation, the duplicate-versus-loss
cost asymmetry, and the threshold at which additional gateway complexity earns
its place — with lab-measured values wherever we can measure them (failover
time, reconnect time, observed rates) and their business parameters left as
explicit named variables. Deliverable **L11**.

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

**Endpoint failover belongs in the sender, and stays trivial.** The sender is
configured with a *list* of gateway endpoints (§5.6). It randomises the order,
tries one, and falls through to the next on a connection failure or error
response. That is the whole client-side availability story, and it is
deliberately not elaborated: connection tuning, backoff policy and
circuit-breaking on the client are the application team's concern, inside a
transport that is a placeholder for whatever they actually use. Bleeding every
edge case through this protocol would be modelling a black box we have already
said we do not need to model.

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
across threads, and one unit of work in flight at a time. In-order delivery is
a *free consequence* of one consumer, **not the justification for it**.

Three positive reasons, none of which is "ordering is required":

- **Throughput is not the constraint.** A settlement lifecycle runs in minutes,
  hours or days — T+1 and T+2 are ordinary. This is the opposite of a
  market-data firehose, and parallelism here optimises a dimension nobody is
  short of. The instinct that *serial must mean slow* is worth naming
  explicitly in the writeup, because it is probably the reflex behind the
  shared open in the sample code.
- **In-order processing makes out-of-order arrival visible.** Precisely because
  the standard permits arbitrary advice order (§11.4), a single consumer turns
  reordering into something you can *observe and report* rather than something
  scrambled beyond detection. Concurrency would destroy the only vantage point
  from which the anomaly is even nameable.
- **Availability comes from standby, not parallelism** (§5.6). N parallel
  consumers would be correctness-safe given §11.4 — but they buy availability
  at the cost of the observability above, and standby delivers the same
  availability without that cost.

This distinction is deliberate, and the standards research (§11.4) has now
settled why: **the settlement lifecycle cannot require in-order processing,
because the standard provides nothing that would make ordering knowable.**

- MT548's status subsequence (A2) contains **only** `25D` status code and
  `24B` reason code. **There is no timestamp in it.** The single date/time in
  mandatory sequence A is `98a::PREP`, defined as *"date/time at which the
  message was prepared"* — composition time, not the time a status was assigned
  or became effective.
- The Category 5 guide defines **no superseding relationship** between multiple
  advices for one instruction, and no usage rule or error code for an advice
  arriving before its logical predecessor.
- FIN's ISN/OSN sequencing is **session integrity** — loss and duplicate
  detection between a logical terminal and the network, recovered by retrieval
  — not an end-to-end guarantee that business events arrive in business order.
  It is also an artefact a content-opaque gateway never exposes to the
  application.

**Therefore ordering must come from status semantics — the `25D` state machine
and the linkage references — never from sorting on any in-message date.** There
is no timestamp key to sort on; the field that looks like one means something
else.

This vindicates the fold in `lifecycle.py`: advancing **monotonically along a
reachability graph**, so a late-arriving earlier status is a no-op, is not
merely convenient — it is the only approach the standard supports. (Note the
distinction: an earlier revision of this spec cited that module as *evidence*
that ordering does not matter, which was circular — it is our own code encoding
our own assumption. The code was right; the argument for it was not. The
evidence is the standard, above.)

Single-threading therefore rests on statelessness and simplicity, which stand
on their own — and the receiver's obligation is to **tolerate arbitrary advice
order**, which §5.3 requires of it.

- **Startup order: connect inward first, to the receiving application, and only
  then to the queue manager.** This is deliberate and it is what prevents the
  partial-failure deadlock described in §5.6. There is no point holding the
  reply queue if the messages cannot be forwarded, so an instance that cannot
  reach the application must **never open the queue at all** — it retries
  inward, loudly, and does not become a contender. Failing to reach the
  application is a configuration fault, and it must be reported with enough
  detail to identify the cause rather than merely logged.
- Then connect to the queue manager and open `GW.SEC.REPLY` **once, with
  `MQOO_INPUT_EXCLUSIVE`** (§5.6), holding both for the process lifetime.
  The option is stated explicitly because `MQOO_INPUT_AS_Q_DEF` resolves via
  the queue's `DEFSOPT` — leaving it unspecified is not neutral, it is
  environment-dependent.
- **If the inward connection is lost and cannot be re-established within a
  bounded window, abort the process.** Aborting frees the queue handle, so a
  standby — already connected inward and blocked on 2042 — takes over. The
  window matters: aborting on the first dropped packet would turn a two-second
  application hiccup into a failover costing the `HBINT`-bounded handle release
  (§5.6), which is worse than waiting. Set it longer than a plausible transient
  and well below the tolerable stall; L11 quantifies both ends.
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

**The named anti-patterns**, as a generic exemplar — never a reproduction of
shared source:

| Anti-pattern | Why it is wrong |
|---|---|
| Objects opened **per message** rather than once at startup | Pure cost: an open and close per message, for a handle that could be held for the process lifetime |
| **Gets outside syncpoint** | Loses messages. The destructive read is unrecoverable if anything downstream then fails (§4.4) |
| **Puts outside syncpoint** | Defensible in isolation — conceded deliberately (§4.4), which is what makes the get-side criticism credible |
| Reply queue opened **`MQOO_INPUT_SHARED`** | **Not a defect — but not a design either.** Since ordering is not required (§11.4), parallel consumers are correctness-safe, so the shared open is legitimate in principle. What is missing is any *use* of it: no second consumer, no availability story it serves, and no acknowledgement of what it costs (§5.2 — it destroys the vantage point from which reordering is observable). The criticism is that the option was selected without a decision behind it, and the exclusive-open standby pattern (§5.6) delivers the availability a shared open only gestures at |
| **Stateful replay in the protocol layer** | The epic's central thesis (§3, §4) |

The `MQOO_INPUT_SHARED` row is worth dwelling on in the writeup, because it is
the most diagnostic. A shared open alongside an assumption of sequential
processing suggests the two decisions were never reconciled — which is the
pattern the whole epic is about, rather than any individual defect.

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

### 5.6 Availability — and why the two paths differ

Removing state from the gateway must not remove **availability** from it. This
section exists because the two directions have structurally different
availability problems, and because the recommendation improves their HA posture
rather than trading it away.

#### Outbound — trivially redundant

Stateless, no syncpoint coordination, no shared resource. Run **N instances**;
the sending application round-robins a host:port list, or tries them in series
and falls through on failure. Redundancy is nearly free. This is the textbook
case for a stateless service, and it is available to them today.

#### Inbound — one active instance, N standing by

There is one reply queue, so there is one consumer. The availability answer is
not parallelism, it is **automatic takeover**, and MQ supplies the entire
mechanism:

1. Every instance opens the reply queue with **`MQOO_INPUT_EXCLUSIVE`** at
   startup.
2. **One wins.** It becomes the active consumer and holds the handle for its
   process lifetime.
3. The others fail with **`MQRC_OBJECT_IN_USE` (2042)** — exclusive open fails
   if the object is already open for input of any sort — log that they are
   standing by, sleep, and retry.
4. When the active instance dies, the queue manager eventually releases the
   orphaned handle. The next standby to retry wins the open and **takes over
   with no coordination, no shared state, and no handover protocol.**

**The lock lives inside the queue manager** — the component that is already
highly available. Split-brain is structurally impossible: MQ will not grant two
exclusive input handles, so there is nothing to fence and no consensus protocol
to get wrong. This is the same paradigm as a multi-instance queue manager,
where two instances contend for a lock and the winner becomes active.

**The retry logic is not extra work — it is mandatory anyway.** A single
instance that crashes and restarts immediately will *also* hit 2042, because
the previous handle has not yet been released. Any application opening a queue
exclusively must handle this; it is basic practice, and it is the same problem
the command server and event monitors face. The HA design therefore costs
nothing new. It only changes the retry *parameters*: a lone instance backs off
briefly and then gives up loudly, whereas a standby backs off indefinitely and
logs that it is waiting. IBM's own programmer response for 2042 is that *"system
design should specify whether an application is to wait and retry, or take
other action."*

**This is why objects are opened once at startup (§5.1, §5.2).** Per-message
open does not merely waste cycles — it makes this pattern impossible. A gateway
that opens and closes per message holds no claim to arbitrate, cannot express
active-versus-standby, and drops its position between every message. The
anti-pattern has an *architectural* cost, not just a performance one.

#### Failover latency — the arithmetic

Takeover is not instant, and the spec should say so with numbers rather than
adjectives:

```text
failover ≈ orphaned-handle release + standby retry interval
```

- **Handle release** is bounded by how quickly the queue manager notices the
  dead client. On a client connection that is driven by the channel heartbeat:
  **`HBINT` defaults to 300 seconds**, and heartbeat flows are what close
  queues left open at the receiving end of a channel. Untuned, that alone can
  dominate failover.
- **Standby retry interval** is ours to choose, and it is a straight trade:
  short means faster takeover and more log noise, long means quieter and
  slower.

Both are tunable, and §9 measures the real figure rather than asserting one.
Given the settlement lifecycle runs in minutes to days (§5.2), a failover
measured in minutes is very likely acceptable — but that should be a *measured
conclusion*, not an assumption.

#### The partial-failure case, and why startup order solves it

The obvious hole in a lock-based standby design is an instance that is **alive
but useless**: it can reach the queue manager but not the receiving
application. Holding the handle, forwarding nothing, blocking a healthy
standby. Process death is the liveness signal, and this failure produces none.

**Startup order closes it** (§5.2). An instance connects *inward first*. If it
cannot reach the application it never opens the queue, so it never becomes a
contender — the broken instance is excluded before it can acquire anything. And
if the inward connection is lost at runtime and cannot be recovered within a
bounded window, the instance **aborts**, which frees the handle through the
same mechanism takeover already relies on.

No new machinery, no relinquish-and-recontend state, no liveness protocol. The
rule is simply: **cannot forward ⇒ not running.**

Two consequences worth stating rather than discovering:

- **Standbys hold an inward connection while idle.** They are connected to the
  application and blocked on 2042, so the application sees N connections of
  which one is active. That is an obligation on the receiving side and it
  should be agreed, not assumed.
- **If no instance can reach the application, nothing runs** — and that is
  correct. It is a loud, unambiguous configuration failure rather than a queue
  quietly failing to drain.

#### Observability — three failure states look identical from outside

These components must be monitored like anything else in the environment, and
that is not a platitude here: **from the outside, "not consuming" has at least
three distinct causes**, and a healthy standby is *supposed* to look idle. So
idleness cannot be the alarm.

Each instance therefore exports an explicit state:

| State | Meaning | Alarm? |
|---|---|---|
| `CONNECTING_APP` | Cannot reach the receiving application; retrying inward | **Yes** — configuration fault, and this instance is not a contender |
| `STANDBY` | Connected inward, holding 2042, waiting for the queue | No — this is health |
| `ACTIVE` | Holds the queue, consuming and forwarding | No |
| `FAILED` | Aborting or aborted | **Yes** |

The alarms that matter follow directly: **no instance in `ACTIVE`** (the reply
path is stalled), **fewer standbys than expected** (redundancy silently
degraded — the failure that hides until the day it matters), and **any
instance in `CONNECTING_APP`** (a route or configuration problem). Reply-queue
depth is a useful corroborating signal but a poor primary one, since depth also
grows legitimately.

This fits the existing observability tier rather than adding a new one — the
repo already exports collector state to Prometheus and boards it in Grafana.

#### The mechanism generalises — which makes the parallelism argument moot

The same design accommodates the topology we do **not** recommend. The only
difference between "one active, N standby" and "N parallel readers" is the open
option: `MQOO_INPUT_EXCLUSIVE` versus `MQOO_INPUT_SHARED`. **Make it a
configuration setting**, and the exemplar supports both from one codebase.

This matters rhetorically as much as technically. "We want multiple agents
draining the reply queue" stops being an architectural disagreement and becomes
a config flag. We can say plainly: *you can do this, the design extends to it,
here is what it costs you, and here is why we do not recommend it* — rather
than appearing to forbid it.

What it costs: the app must then own cross-instance deduplication and
sequencing, and you lose the only vantage point from which reordering is
observable (§5.2). What it buys: throughput, which §10 records is not a
constraint in this domain.

#### Why three, not two

Two instances give redundancy only until the first failure. From that moment
until repair completes you are running **unprotected**, and a second failure in
that window is a full outage. Three is the minimum count that is *still
redundant after one failure*.

The quantitative form, which feeds the risk model (L11, §4.6): outage
probability scales with the **exposure window** — the ratio of repair time to
time between failures. With two nodes a single failure opens that window; with
three, an outage requires two failures inside it, so the improvement is roughly
quadratic rather than linear. This is the reasoning behind three replica sites
generally, and it applies unchanged here.

**And the two paths have different exposure**, which is easy to miss:

| | Outbound | Inbound |
|---|---|---|
| Instances active | All | One; the rest stand by |
| Cost of losing one | ~1/N of capacity | A genuine outage window |
| Recovery time | ≈ zero — the sender tries the next endpoint | The measured takeover time (§9) |

So the inbound path is where the redundancy count earns its keep: with three
contenders, a second failure during a takeover still leaves a standby to win
the open.

**One shared dependency, stated honestly:** all instances connect to the same
queue manager. Gateway redundancy composes with — and is subordinate to — queue
manager availability, which is the base lab's subject and is already addressed.
Redundant gateways do not help if the queue manager is gone.

**One operational detail:** N standby contenders all retrying the same open
want a **jittered** retry interval, so they neither synchronise into a
thundering herd nor race deterministically for the handle.

#### What the lab can and cannot demonstrate

The inventory today has one host per group (`lab_qm`, `lab_obs`, `svc_qm`) and
no host dedicated to running the gateway. So:

- **Demonstrate process-level failover.** Run the contenders as separate
  processes and exercise the whole mechanism — exclusive open, 2042, standby
  logging, takeover, measured recovery. The queue manager arbitrates **by
  connection, not by host**, so this is a *faithful* demonstration of the
  mechanism, not a simplification of it.
- **Treat host-level distribution as a deployment statement**, not something
  the lab must prove. What co-located processes cannot show is *failure
  independence* — and that is a property of their deployment, not of this
  design.
- **L11 must state that assumption.** The quadratic redundancy benefit above
  assumes independent failure domains. Three processes on one host share a
  domain and do not get it. Writing the arithmetic without that caveat would be
  precisely the hand-waving §4.6 objects to.

#### The obligation this hands back

The state belongs in the receiving application (§4, §5.3) — and **the
availability obligation follows the state**. Their receiving component is
singular *and* stateful, which is the genuinely hard availability problem. This
epic does not create that obligation; the state was always theirs. But it does
make it visible, and it is fairer to say so plainly here than to let them
discover it later.

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
- **L10 — Make ordering semantics honest.** #275. **Reframed** by L9's
  findings: this was scoped as swapping the sort key to a business timestamp,
  and no such timestamp exists in MT548 (§11.4). There is nothing to sort on,
  so the work is to stop implying otherwise — keep put-time as what it
  honestly is (**arrival order**, the only observable available), correct the
  docstrings and any timeline output that presents arrival time as business
  event time, and add a test that **shuffles a response stream and asserts the
  terminal state is unchanged**. That test turns the standards finding into an
  executable guarantee, which is worth more than the sort key ever was.
- **L11 — Risk model (§4.6).** The arithmetic under the design decisions:
  expected failure frequency from component MTBF and message volume; expected
  duplicates per year under stateless retry; the cost asymmetry between a
  duplicate (detectable, `PDE`-markable, costs operational attention) and a
  lost message (undetectable, unrecoverable); and the threshold at which extra
  gateway complexity earns its place. **Lab-measured values where we can
  measure them** — failover time from §9, reconnect time, observed rates — and
  their business parameters as explicit named variables, never invented.
  Publishable, and the piece that lets a reader disagree with our conclusion by
  substituting their own numbers rather than their own instincts.
- **L12 — Inbound HA: exclusive-open standby.** §5.6. The active/standby
  takeover mechanism, the 2042 retry discipline every exclusive-open
  application needs regardless, jittered retry, and the measured failover
  figures from §9. Open mode is **configurable** (`EXCLUSIVE` for
  active/standby, `SHARED` for parallel readers) so the exemplar demonstrates
  the topology we recommend while supporting the one we do not.
- **L13 — Multi-instance deployment.** Deploy three inbound contenders and
  multiple outbound instances, with the sender configured for endpoint
  failover. Process-level in the lab, since the inventory has no dedicated
  gateway hosts — a faithful demonstration, because the queue manager
  arbitrates by connection rather than by host (§5.6).
- **L14 — Gateway observability.** Export the §5.6 state model
  (`CONNECTING_APP` / `STANDBY` / `ACTIVE` / `FAILED`) plus the alarms that
  follow from it: no instance active, fewer standbys than expected, any
  instance unable to reach the receiving application. Fits the existing
  Prometheus/Grafana tier rather than adding one. Without this the HA design is
  unobservable — a healthy standby and a stuck instance are indistinguishable
  from outside, and silently degraded redundancy is the failure that hides
  until the day it matters.

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
- **Inbound standby takeover (§5.6).** Start two inbound instances; confirm one
  wins the exclusive open and the other logs 2042 and stands by. Kill the
  active one and **measure the wall-clock time until the standby is consuming**.
  Repeat with `HBINT` at its 300-second default and at a tuned value, so the
  handle-release contribution is isolated from the retry interval. Publish the
  numbers — this is the headline availability figure of the whole design, and
  the one thing that turns "it fails over automatically" into a claim with a
  unit attached.
- **Severed inward route (the partial-failure case, §5.6).** With an active
  instance running, break *only* its route to the receiving application while
  leaving its queue-manager connection healthy. Required observation: it
  reports the fault, exhausts its bounded reconnect window, aborts, and a
  standby takes over. This is a **stronger test than killing the process**,
  because process death is the easy case — this is the one the design was
  specifically shaped to survive.
- **Start with the inward route already broken.** Confirm the instance never
  opens the queue, reports `CONNECTING_APP`, and does not become a contender.
- **Restart-into-2042.** Kill the *only* instance and restart it immediately;
  confirm it hits 2042 and recovers by retry rather than dying. This is the
  case every exclusive-open application must handle regardless of HA, and it
  proves the standby logic costs nothing extra.
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
- **Latency is not a design constraint — and this is a stated architectural
  input, not an aside.** This is back-office settlement. The obligation is to
  complete within a window measured in **hours and days** (T+1, T+2), governed
  by contract, not in microseconds governed by competition. It is the opposite
  of a market-data or trading path. Every argument in this spec that declines
  to optimise throughput — the single consumer (§5.2), the serial ack-gated
  inbound commit (§5.2), the standby rather than parallel availability model
  (§5.6) — rests on this, so it is recorded here once rather than re-argued
  each time. The reflex that *serial must mean too slow* is the likeliest
  unexamined assumption behind the sample code's shared open, and naming the
  timescale is what defuses it.
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
4. **Message ordering — largely resolved.** Settled against the SWIFT
   Category 5 Message Reference Guide, FIN System Messages, and FIN Error
   Codes (#274). Findings, data first:

   - **No status-effective timestamp exists.** MT548's status subsequence (A2)
     carries only `25D` and `24B`. The sole date/time in mandatory sequence A
     is `98a::PREP` — *"date/time at which the message was prepared"*, i.e.
     composition time. A vendor-specific usage note describing PREP as the time
     a status was assigned is **local practice, not the standard**, and must not
     be generalised.
   - **No superseding rule.** A search of the full Category 5 guide returns no
     superseding relationship between advices, and the MT548 usage rules govern
     only linkage references. There is no network-validated rule and no error
     code for an advice arriving before its logical predecessor.
   - **ISN/OSN is session integrity, not business ordering.** Sequence numbers
     are strictly maintained between a logical terminal and the network, with
     mismatches NAK'd, a sequence-gap timeout, and recovery by retrieval
     (MT020 → MT021). That detects loss and duplication on a session; it does
     not promise that business events arrive in business order — and a
     content-opaque gateway never surfaces it to the application anyway.

   *Consequences:* ordering must derive from **status semantics**, never from a
   timestamp sort (§5.2); the receiver must **tolerate arbitrary advice order**
   and absorb an early status rather than report it (§5.3); and the design's
   order-tolerance is a standards requirement rather than a convenience.

   *Still open, narrowly:* whether a **positive** end-to-end ordering assurance
   is stated anywhere. If it exists it would be in the FIN Service Description
   or Operations Guide, which we have not obtained; the three documents read
   contain no such statement. SMPG market practice for MT548/MT537 remains
   worth a look for business-layer colour, but no longer gates anything.

   *The anti-pattern this settles:* the sample C++ opens the reply queue with
   `MQOO_INPUT_SHARED`, permitting a second consumer. Multiple consumers
   **race** — priority governs queue position, not which consumer wins. Since
   ordering turns out not to be required, the shared open is not a correctness
   defect; it is **incoherent**, because the code takes no advantage of the
   concurrency it enables while contradicting the sequential processing it
   appears to assume. That is a sharper criticism than "it is a bug", and a
   fairer one (§5.4).

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
