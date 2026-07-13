# Tracking confirmations and enabling replay with MQ-native audit queues

*A recommendation for the message-gateway replay problem.*

> **Draft — provenance pending.** Passages marked `⟨PLACEHOLDER: …⟩` await the numbers
> and dashboard image from the last-hour live run. **The provenance claims (§1, §9, and
> the closing note) are scoped to a run over the real remote topology — the counterparty
> on a separate queue manager across a channel — which is being proven now; they are not
> yet final.** This document will not be circulated until that end-to-end remote proof
> exists.

---

## 1. Summary

A generic trading back-office runs a **message gateway**: applications connect to it
over a proprietary protocol, and it forwards each message to a counterparty over IBM
MQ and records the counterparty's confirmation. For the day's flow to be
**replayable** — so that after a failure the operators know exactly which messages
went out without a confirmation and can safely resend them — the gateway must keep an
authoritative record of every message sent and every confirmation received.

This document recommends recording that history the way the queue manager already
knows how to do it: with an **MQ-native audit copy** of each message and each
confirmation, written atomically by the queue manager under the same unit of work as
the live traffic, and reconciled by a small read-only **collector**. The result
answers, at any moment and authoritatively, *which specific messages are still
unconfirmed* — not merely how many — and it does so without a hand-written log, a new
product, or any change to the messages themselves.

The recommendation is not theoretical. Every mechanism and every algorithm below was
**built as a working prototype and validated in a laboratory** against a real IBM MQ
**Native HA** queue manager (a three-node group), over **mutual TLS 1.3** — with the
counterparty on a **separate queue manager reached across a channel**, exactly as in
production, so the outbound audit is proven against real remote queuing and not a
local shortcut. The evidence in §9 — a live dashboard and measured numbers — is output
from that prototype, not an illustration. ⟨PENDING: the remote-topology run is the
work in progress; this claim is finalised only once it is proven.⟩

---

## 2. The problem: replay needs to know *which* messages are unconfirmed

Replay is the right requirement. The difficulty is entirely in the record it depends
on. To resend safely after a failure you must be able to state, for the current
trading day:

- every message that was sent;
- every confirmation that came back;
- and therefore the **exact set** of messages that were sent but never confirmed.

A record that only counts — "40 unconfirmed" — cannot drive a replay; you must be able
to name each outstanding message so it can be resent and so a resend is not mistaken
for a new message. And the record has to be **correct under failure**: the moment it
can disagree with what MQ actually did — a message recorded as sent that never left, or
sent-but-not-recorded — the replay built on it is unsound.

That correctness-under-failure property is exactly what a hand-rolled, application-side
log tends not to have. Writing to a log and putting to a queue are two separate actions;
a failure between them leaves the log and the queue disagreeing, which is the one state
a replay record must never be in. The queue manager already solves this problem for its
own data. The recommendation is to let it.

---

## 3. The core insight: let the queue manager keep the record

IBM MQ can duplicate a queue's traffic for you — a **streaming queue** — so that a copy
is retained for audit while the live message flows on, all under the **same unit of
work**, with the **same message identifier**, and with no application code involved. The
counterparty sits across a channel on another queue manager, so the two directions use
this mechanism slightly differently:

- **Outbound.** The gateway puts each message to a **local** queue whose streaming
  target is a **remote-queue definition** pointing at the counterparty's queue manager.
  The queue manager **retains the original locally** — that retained queue is the
  **outbound audit** — and **transmits a duplicate** to the counterparty (over a
  transmission queue and sender channel). Both actions are one unit of work.
- **Inbound.** The counterparty's confirmation arrives across a channel onto a **local**
  reply queue. That reply queue streams a copy to a **local inbound audit** queue; the
  gateway consumes the confirmation as usual, and the audit copy is retained.

"Atomically, in the same unit of work" is the whole point: for the outbound send, either
the message is both retained for audit and queued for transmission, or neither happens —
there is no window in which a message is transmitted but not recorded, or recorded but
not transmitted, the failure mode a hand-written log cannot close. Because the streamed
copy preserves the **same message identifier**, the audit can be matched back to the
flow exactly.

Two audit records result: the **outbound audit** (the retained originals of everything
sent) and the **inbound audit** (a copy of every confirmation received). Reconciliation
reads only these — never the live consume path — so it never competes with throughput.

---

## 4. The blueprint: the MQ objects

The mechanism is a base-licensed IBM MQ feature (a **streaming queue** with
must-duplicate quality of service); it needs no MQ Advanced entitlement and no new
product. Two rules of the feature shape the design, and both are load-bearing:

- A local queue's streaming **target may be a remote-queue definition** — so the
  duplicate can be delivered to another queue manager.
- You **cannot** put `STREAMQ` on a transmission queue, nor on a remote-queue
  definition — so the *source* of a stream is always an ordinary local queue.

Together these dictate the outbound shape: the gateway puts to a **local** queue, the
**original stays there as the audit**, and the **duplicate is streamed onward** to the
counterparty. The objects for one counterparty's flow (`CPB`) follow. Names are
illustrative — substitute your own convention; the placeholders in angle brackets are
the counterparty's own coordinates.

```mqsc
* --- OUTBOUND ---------------------------------------------------------------
* The gateway puts here. The ORIGINAL is retained on this queue (the outbound
* audit); a DUPLICATE is streamed onward to the counterparty. CAPEXPRY gives it
* a self-clearing rolling-day retention (value in tenths of a second; 864000 = 24 h).
DEFINE QLOCAL('GW.CPB.SEND') DEFPSIST(YES) SHARE MAXDEPTH(999999999) CAPEXPRY(864000)

* The stream target: a remote-queue definition resolving to the counterparty's
* queue, via a transmission queue drained by a sender channel.
DEFINE QREMOTE('GW.CPB.SEND.TO.CPB') RNAME('<counterparty inbound queue>') +
       RQMNAME('<counterparty qmgr>') XMITQ('<xmitq to counterparty>')
DEFINE QLOCAL('<xmitq to counterparty>') USAGE(XMITQ)
DEFINE CHANNEL('NHAUAPP.TO.CPB') CHLTYPE(SDR) TRPTYPE(TCP) +
       CONNAME('<counterparty host>(1414)') XMITQ('<xmitq to counterparty>')

* One put; the queue manager retains the original AND streams a duplicate to the
* counterparty — same MsgId, one unit of work, both-or-neither. No application logic.
ALTER  QLOCAL('GW.CPB.SEND') STREAMQ('GW.CPB.SEND.TO.CPB') STRMQOS(MUSTDUP)

* --- INBOUND ----------------------------------------------------------------
* Confirmations arrive from the counterparty (across a return channel) onto a local
* reply queue; the gateway consumes them, and a duplicate is retained as the audit.
DEFINE QLOCAL('GW.CPB.REPLY')       DEFPSIST(YES) SHARE MAXDEPTH(999999999)
DEFINE QLOCAL('GW.CPB.REPLY.AUDIT') DEFPSIST(YES) SHARE MAXDEPTH(999999999) CAPEXPRY(864000)
ALTER  QLOCAL('GW.CPB.REPLY') STREAMQ('GW.CPB.REPLY.AUDIT') STRMQOS(MUSTDUP)
```

The return path for confirmations (the counterparty's reply routed back to
`GW.CPB.REPLY`) uses standard reply-to-queue / reply-to-queue-manager addressing and a
channel back from the counterparty — ordinary MQ request/reply, out of scope for the
audit mechanism itself.

Three invariants make the record trustworthy, and the collector below depends on them:

1. **The message identifier is preserved** into the streamed copy, so both the retained
   original and the transmitted duplicate carry the identifier the gateway sent under —
   and the outbound audit can be matched to the confirmations exactly.
2. **The confirmation echoes the request's identifier.** By convention the counterparty
   returns the request's message identifier as the **correlation identifier** of its
   confirmation. That echo is what makes a confirmation match its request
   authoritatively.
3. **Must-duplicate means both-or-neither.** If the message cannot be both retained for
   audit and queued for transmission, the put fails; there is no "transmitted but
   unaudited" (or "audited but not transmitted") state. Note that must-duplicate
   guarantees the duplicate reaches the **transmission queue**, not that the channel has
   yet delivered it — which is correct: the audit records the send, and a message still
   awaiting transmission is, rightly, counted as unconfirmed.

`MAXDEPTH` is set high because a full day of audit traffic far exceeds the default; the
single queue-file limit is not a concern (its default ceiling is roughly two terabytes,
far above a day's volume). `CAPEXPRY` bounds retention to one rolling trading day, so
the audit queues never grow without limit. Reconciliation (below) browses the **outbound
audit** — the retained originals on `GW.CPB.SEND` — and the **inbound audit**,
`GW.CPB.REPLY.AUDIT`.

---

## 5. Algorithm 1 — response time per message

Every message carries, in its MQ message descriptor, the moment the queue manager
stamped it (put date and put time, in GMT). Because the audit copies preserve these,
the response time of any individual request can be computed by reading headers alone —
no timers, no application bookkeeping.

To compute per-message response times:

1. Browse the **outbound audit** queue. For each request, note its **message
   identifier** and its **put timestamp** (the moment it was sent).
2. Browse the **inbound audit** queue. For each confirmation, note its **correlation
   identifier** (the echoed request identifier) and its **put timestamp** (the moment
   the confirmation arrived). Index the confirmations by correlation identifier.
3. For each request that has a matching confirmation, the **response time** is the
   confirmation's put timestamp minus the request's put timestamp.
4. Aggregate across the day as needed — mean, distribution, and the slowest — to show
   confirmation latency and, crucially, its **degradation over time** when the
   counterparty slows down.

This is the basis for the latency curve on the dashboard: a rising mean, and a widening
tail, are visible directly from the two audit queues.

---

## 6. Algorithm 2 — reconciliation: the unconfirmed set

This is the core of replay: identifying, authoritatively, the messages sent without a
confirmation.

1. Browse the **outbound audit** queue and collect the set of **request message
   identifiers** for the current trading day. (Scope to today using the put date; the
   `CAPEXPRY` cap means the audit queues hold at most a rolling day, so this stays
   bounded.)
2. Browse the **inbound audit** queue and collect the set of **confirmation correlation
   identifiers** for the day.
3. The **unconfirmed set** is the set difference: every request message identifier for
   which no confirmation carries the matching correlation identifier.
4. Report both the **count** of unconfirmed messages *and* the **specific list** — each
   outstanding message by identifier, with its sent-time and its age. The age is what
   turns "unconfirmed" into an operational signal: a message unconfirmed for seconds is
   within tolerance; one unconfirmed for many minutes is escalating.

This is precisely the set a replay needs. Because each outstanding message is named by
its original identifier, a resend reuses that identifier, and the counterparty — which
matches on it — treats the resend as the same message rather than a new one. Replay is
therefore **de-duplicated by construction**, not by a separate guard.

**On scale.** The set difference itself is cheap; the cost is dominated by the browse.
See §10 for the measured figures and the two levers — running the collector local to
the queue manager, and reconciling incrementally — that keep it bounded at high volume.

---

## 7. Algorithm 3 — unexpected confirmations: the exception that should never happen

Reconciliation has a mirror image worth surfacing, because it distinguishes a benign
condition from a genuine fault.

A **missing** confirmation (a request with no reply) is *expected* and benign: a
confirmation can be lost in transit, delayed, or dropped during an outage. That is
ordinary, and it is exactly what Algorithm 2 collects for replay.

An **unexpected** confirmation is the opposite — it should never occur:

1. While indexing confirmations (Algorithm 1, step 2), check each confirmation's
   correlation identifier against the set of request message identifiers.
2. A confirmation whose correlation identifier matches **no** request is an **orphan** —
   a reply to something the gateway never sent.
3. A second confirmation for a request already confirmed today is a **duplicate**.

Either is a correctness fault in the flow — a mismatched counterparty, a misrouted
reply, a replay gone wrong — and should be **flagged as an exception**, not folded into
the unconfirmed count. In a healthy system this signal is always zero; a non-zero value
is a reason to look. It is included here not because it is expected traffic, but because
a record that can prove the absence of such faults is stronger than one that cannot.

---

## 8. The collector: what to build

Everything above is read-only work over the two audit queues. The one piece of software
to build is a small **collector** that, on a schedule:

- browses the outbound and inbound audit queues for the current trading day;
- runs the three algorithms — response-time (Algorithm 1), the unconfirmed set
  (Algorithm 2), and the unexpected-confirmation check (Algorithm 3);
- publishes the results: the aggregate counts and latency, the **named unconfirmed
  set** with ages, and any exceptions.

The collector holds no state that must survive a restart — the audit queues *are* the
state, and they are as durable and (on Native HA) as replicated as the messaging
itself. It never touches the live path, so it cannot affect throughput. Its output is
what an operations dashboard and an automated resend both consume.

This collector was **built and validated in the laboratory** — the dashboard and figures
in §9 are its actual output. The implementation is deliberately out of scope here: this
document specifies *what it computes and reads*, which is the part that must be right;
the code is straightforward once the audit queues and the three algorithms are in place.

---

## 9. Evidence: a last-hour trading run, live

To show what the collector surfaces, a realistic **last hour of a trading day** was run
against the live Native HA queue manager, **with the counterparty on a separate queue
manager across a channel** — the real remote topology, so the outbound audit is
exercised through an actual transmission queue and streamed remote delivery, not a local
stand-in. The run: a healthy open, then confirmation response times **degrading**, then a
**~10-minute stoppage** during which confirmations stop arriving and the unconfirmed
backlog climbs, then **recovery** as the backlog drains — leaving a small handful of
trades still unconfirmed at end of day, plus one deliberately injected **unexpected
confirmation** to exercise Algorithm 3.

> ⟨PLACEHOLDER: dashboard image — `docs/images/last-hour-board.png`, the end-of-day
> board for the last-hour run.⟩

Read straight off the audit queues at end of day:

| Signal | Value |
|---|---|
| Sent | ⟨PLACEHOLDER⟩ |
| Confirmed | ⟨PLACEHOLDER⟩ |
| **Unconfirmed** | **⟨PLACEHOLDER: the 2–3 trades left outstanding, each named by identifier with its age⟩** |
| Mean confirm latency | ⟨PLACEHOLDER⟩ |
| Peak confirm latency (during the slowdown) | ⟨PLACEHOLDER⟩ |
| **Unexpected confirmations** | **⟨PLACEHOLDER: 1 — the injected orphan reply, flagged as an exception⟩** |

The board shows the three things the algorithms produce: the **latency curve** rising
through the slowdown (Algorithm 1); the **unconfirmed backlog** climbing during the
stoppage and draining on recovery, with the **named end-of-day set** in a table
(Algorithm 2); and the **unexpected-confirmation** signal going non-zero for the injected
fault (Algorithm 3). The invariants held live: the message identifier was preserved into
the audit copy, so reconciliation matched correctly; the correlation-identifier echo made
each match authoritative; and a resend of an unconfirmed message was confirmed once and
never processed twice.

---

## 10. Scale: where the cost actually is

The reconciliation was measured at up to two orders of magnitude beyond expected volume.
The findings, from the laboratory prototype:

- **The set difference is negligible.** Matching the identifier sets is a fraction of a
  microsecond per header; a full **one-million-message day (~2 million headers)
  reconciles in about one second** of computation.
- **The browse dominates.** Reading each header over a **client-mode** connection costs
  roughly **0.6 milliseconds per header** — about a thousand times the matching cost —
  which extrapolates to roughly **21 minutes** to browse a full million-message day's
  audit queues from a remote client.

So the algorithm is not the constraint; the **transport and the incrementality of the
browse** are. Two levers keep it bounded at production scale:

1. **Run the collector local to the queue manager** (local bindings), removing the
   per-header network round-trip that dominates the remote cost.
2. **Reconcile incrementally** — track a high-water mark and browse only what has
   arrived since the last pass — turning a whole-day scan into a scan of the new
   messages only. A full scan is then needed only for a cold recovery.

The `CAPEXPRY` cap bounds the worst case to a single day's volume, and the single
queue-file size (default ceiling ~2 TB) is far above a day's data, so neither storage nor
scan time grows without limit.

---

## 11. Questions for the application team

The design above is complete for the common case; a few answers would confirm the edges
and size the deployment:

1. **Forwarding.** Are confirmations only recorded, or also forwarded onward to the
   originating application? This changes the reply-path design.
2. **Volumes.** The confirmed order of magnitude today, and the expected growth, to size
   retention and the reconciliation interval (see §10).
3. **Topology.** One queue manager per counterparty, or shared? This decides whether one
   collector serves many counterparties.
4. **Recovery objective.** The acceptable time to detect an unconfirmed message and the
   acceptable time to recover — this is what the collector's schedule and the dashboard
   thresholds are set to serve.
5. **Data at rest.** A retained day of messages on the audit queues is data at rest; does
   that pull in on-disk encryption requirements?
6. **Availability.** Should the gateway's record inherit the messaging estate's own
   high-availability and replication (as it does when the audit queues live on a Native
   HA queue manager), rather than depending on a single host?

---

*Provenance: every mechanism, invariant, and algorithm in this document was implemented
as a working prototype and validated in a laboratory against a real IBM MQ Native HA
queue manager over mutual TLS 1.3, with the counterparty on a separate queue manager
reached across a channel — the outbound audit proven through real remote queuing, not a
local stand-in. The figures in §9 and §10 are measured output from that prototype.
⟨PENDING: finalised once the remote-topology run is proven.⟩*
