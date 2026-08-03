# Epic #235 — least-privilege MQ authority model — spec

*A generic trading back-office gateway problem. No employer/client/vendor
identifiable content; invented queue/host/identity names only.*

---

## 1. Origin — why this epic exists

The streaming-audit design's headline property is a **browse-only passive
observer**: many independent tools can read the rolling history at once, none of
them able to alter or consume it, while exactly one application owns the
transactional processing. That guarantee is asserted in the presentation
material and the design docs.

Today it is enforced in **exactly one place: the reader's own code**, which
opens the audit queues `MQOO_BROWSE`. Nothing at the queue-manager level stops a
different program — or a bug in a reader — running under the same identity from
issuing a destructive `MQGET`, a `PUT`, or a `CLEAR`. A review of the Ansible
roles and the setup/config docs confirms:

- `gw-sec-queues` creates the `GW.SEC.*` objects but sets **zero `SET AUTHREC`**
  records.
- There is **no `DEFINE CHANNEL`** anywhere in this repo's Ansible — the
  identities reach the queue manager over the **base lab's** channels.
- **No setup/config doc** describes the object-permission model.
- **The business identity carries observer rights.** The committed deploy's
  audit-queue reader is the `gw-sec-observability` exporter, which browses both
  audit ledgers over **`APP.SVRCONN`** with the **`app-client`** cert — the *same
  identity the application uses to put instructions and get replies*. So the
  business identity and an observer are the same effective principal. (The
  streaming `gw-sec-collector`, the dedicated `MON.SVRCONN` observer, was
  hot-deployed live during epic #175 and is **not yet in `site.yml`** — so the
  committed deployment does not even reflect the browse-only observer the design
  is built around.)

So "browse-only passive observer" is currently a **code convention, not an
access-control boundary**, *and* the observer role is not separated from the
business role at the identity level — precisely the holes a security-conscious
reader probes first. This epic closes both by making the least-privilege model
**explicit, enforced at the queue manager, and proven with a negative test**,
and by **separating every observer onto a dedicated read-only identity** so the
business identity carries no browse rights at all.

It is **largely academic by design**: the deliverable is a correct reference
model demonstrated on the objects we own, not a fix for a live incident.

## 2. Mission

Enforce and prove the **full** least-privilege authority model for the
settlement gateway — application, observers (collector *and* exporter), inbound
MCA, and responder identities — each granted only the object authorities its
role requires, on the `GW.SEC.*` objects this repo owns, replayable cleanly on a
from-scratch rebuild, and validated live including the denial paths. As part of
this, **separate the observer role from the business role at the identity
level**: every audit-queue reader connects as the dedicated `MON.SVRCONN`
monitoring identity, leaving `APP.SVRCONN` strictly put/get.

## 3. The boundary principle (self-owning bolt-on rule)

The identities reach the queue manager over the **lab's** channels
(`APP.SVRCONN` for the application, `MON.SVRCONN` for the observers, and the
inter-QM return channel for inbound replies). Under the self-owning bolt-on rule
those channels — and their `CHLAUTH`/`MCAUSER`/`SSLPEERMAP` identity mappings —
**belong to the base lab; this epic does not mutate them.** Separating the
observer identity (below) is achieved entirely on **our** side — we point our
`gw-sec-observability` exporter role at the existing `MON.SVRCONN` with the
existing `mon-client` keystore, exactly as the collector already does; we
**define no channel and change no `CHLAUTH`**. The split:

- **Enforcement we own.** `SET AUTHREC` on the `GW.SEC.*` objects this repo
  creates, scoped per principal. This is 100% owned and automated by our Ansible,
  and replays on rebuild.
- **Identity we declare-and-verify.** The **effective** principal each channel
  resolves an incoming connection to is *discovered and asserted* at apply time
  — never changed. Because the effective identity may come from a `CHLAUTH`
  `SSLPEERMAP` rule rather than the channel's static `MCAUSER` attribute, the
  reliable discovery is **observational**: `DISPLAY CHSTATUS(<channel>) MCAUSER`
  on the live connection, corroborated against the channel/`CHLAUTH`
  definitions. A missing or unexpected identity is a **loud failure**, not a
  silent fix.

This is what keeps the epic honest: we prove least privilege on *our* objects
against the identities the lab *hands us*, without reaching across the boundary.

**Load-bearing precondition — the effective identities must be non-privileged.**
OAM authority checks are **bypassed for a privileged user** (e.g. `mqm`, or any
member of the `mqm` group). If a channel resolves incoming connections to a
privileged effective user, `SET AUTHREC` is a no-op for that identity: the
grants in §5 neither constrain it nor produce the 2035 denials §7 relies on — so
the model is **unenforceable and unprovable** on our side without changing the
lab's channel identity mapping, which the boundary forbids. Therefore each
effective identity being **non-privileged and outside `mqm`** is a *precondition*
of this epic, asserted before any authrec is trusted (§7). If an identity is
privileged, that is a **declare-and-verify finding**: report it, stop, and treat
enforcing the model as blocked on a separate base-lab decision — never silently
widen the boundary to make the test pass.

## 4. The topology the model must fit

```
                 application (APP.SVRCONN, app-client cert)
                   │  MQPUT                         ▲  MQGET
                   ▼                                │
        GW.SEC.INSTRUCT (QALIAS) ───► GW.SEC.INSTRUCT.AUDIT   GW.SEC.REPLY
                                        │ (retained original)   ▲
                                        │ STREAMQ MUSTDUP        │ inbound MCA
                                        ▼                        │ (return channel)
                                   GW.SEC.INSTRUCT.REMOTE ──► counterparty ──┘
                                                                 │ STREAMQ MUSTDUP
                                   GW.SEC.STATUS.AUDIT ◄─────────┘  (from GW.SEC.REPLY)

        observers (MON.SVRCONN, mon-client cert) ── browse only ──► GW.SEC.INSTRUCT.AUDIT
          • gw-sec-collector  (streaming, stateful)                 GW.SEC.STATUS.AUDIT
          • gw-sec-observability exporter (per-scrape)
```

Note the crux: **`GW.SEC.INSTRUCT.AUDIT` is legitimately reached by two *kinds*
of principal with two different rights** — the application (`APP.SVRCONN`) *puts*
to it via the alias, as the retained original; the observer identity
(`MON.SVRCONN`, shared by the collector and the exporter) *browses* it; and
**neither may `GET`**, nor may the application *browse*. The only way a message
leaves an audit ledger is expiry (`CAPEXPRY`); any `CLEAR`/delete is a separate
privileged administrative act.

## 5. The full least-privilege model (target end-state)

On our queue manager, for the objects `gw-sec-queues` owns:

| Principal (via) | Object | Authorities | Explicitly NOT |
|---|---|---|---|
| Application (`APP.SVRCONN`) | `GW.SEC.INSTRUCT` alias → `GW.SEC.INSTRUCT.AUDIT` | `+put` | `+browse +get +set +dlt +clr` |
| Application (`APP.SVRCONN`) | `GW.SEC.REPLY` | `+get +inq` | `+browse`, `+put` |
| Observers (`MON.SVRCONN`) — collector **and** exporter | `GW.SEC.INSTRUCT.AUDIT`, `GW.SEC.STATUS.AUDIT` | `+browse +inq +dsp` | `+get +put +set +dlt +clr` |
| Inbound MCA (return channel) | `GW.SEC.REPLY` | `+put +setall` | everything else |

Both audit-queue readers — the streaming `gw-sec-collector` and the per-scrape
`gw-sec-observability` exporter — connect as the **single `MON.SVRCONN`
observer identity**. The application (`APP.SVRCONN`) is left with **no browse
right anywhere** on the audit ledgers, which is what makes the §7 app-side
browse-denial a meaningful, enforced boundary rather than an accident of nobody
having tried.

For the objects `gw-sec-svc-queues` owns on `SVCQM` (the responder side): the
responder (`SVC.SVRCONN`) gets its own least-privilege grants on its landing/reply
objects, following the same shape. (The responder is the lab's counterparty-side
app; its exact grants are enumerated in the plan.)

Model-level invariants:

- **Authorities are per-operation and additive.** `+put` on an audit ledger does
  **not** confer `+browse` or `+get` — the grants are independent, not
  hierarchical. The validation must therefore confirm the application has **no**
  `+browse`/`+get` on `GW.SEC.INSTRUCT.AUDIT` from **any** source (no `+all`
  profile, no group-inherited grant, no pre-existing authrec): its only grant on
  that queue is the `+put` it uses. Without this invariant the §7 app-side
  browse-denial could pass or fail for a reason the model never named.
- **The streamed MUSTDUP duplicate is written under the queue manager's own
  authority.** The putting application needs **no** authority on the stream
  target — confirm this holds under least privilege (it is a QM operation).
- **`+connect`** on the queue manager is a base-lab grant (the identities
  already connect); we declare-and-verify it, we do not add it.
- **Administrative cleanup** (`CLEAR`, `DELETE`) requires a **separate
  privileged identity**, never the collector or the application.

## 6. Deliverables

1. **Observer-identity separation (`gw-sec-observability`)** — switch the
   settlement exporter from `APP.SVRCONN`/`app-client` to the dedicated
   `MON.SVRCONN`/`mon-client` monitoring identity (the same one the collector
   already uses): change the role's `gw_sec_exporter_channel`, `gw_cert_label`,
   and `gw_key_repo` defaults, and ensure the `mon-client` keystore is present on
   the exporter host. After this, no observer connects as the business identity.
2. **Wire the collector into `site.yml` (`gw-sec-collector`)** — add the
   `gw-sec-collector` play so the committed deploy matches what runs live (it was
   hot-deployed during epic #175). Without this, "replays cleanly on a
   from-scratch rebuild" is false for the observer the model centres on.
3. **`gw-sec-queues`** — after the existing `DEFINE`s: declare-and-verify the
   effective `MON.SVRCONN` and `APP.SVRCONN` (and inbound-MCA) identities, then
   `SET AUTHREC` the grants in §5 via `runmqsc` (consistent with the role's
   existing pipe-to-`runmqsc` style). Idempotent (authrec set converges on each
   apply), owned, replays on rebuild.
4. **`gw-sec-svc-queues`** — the responder-side grants in §5.
5. **Docs** — a "Security & permissions" section in the settlement-tracking
   setup doc (and a pointer from `ansible/README`) documenting the model **and**
   the ownership boundary (what we authrec vs. what the lab provides), including
   the observer-identity separation.
6. **Live validation task** — see §7.

## 7. Validation — the proof is the denial

A `validation`-kind operational task, run after the role changes are merged and
applied to the live lab.

**Precondition self-check (run first; blocks the task if unmet).** Assert each
effective identity (the `MON.SVRCONN` observer, the `APP.SVRCONN` application,
the inbound MCA) is **non-privileged and not a member of `mqm`** — otherwise OAM
bypasses the authrecs and the denials below cannot fire (see §3). Discover the
effective identity (`DISPLAY CHSTATUS … MCAUSER` on the live connection,
corroborated against the channel/`CHLAUTH` definition) and check its group
membership. If any identity is privileged, comment "blocked: preconditions not
met — <identity> is privileged; model unenforceable on our side without a
base-lab change" and **stop** — never fabricate a pass.

With the precondition met:

- **Positive paths still work.** App puts an instruction; it streams to the
  counterparty and is retained; a reply lands on `GW.SEC.REPLY` and streams to
  `GW.SEC.STATUS.AUDIT`; **both** observers on `MON.SVRCONN` — the collector and
  the now-reidentified exporter — browse both audit ledgers and their metrics
  advance. No regression from the new authrecs or the exporter's identity switch.
- **Negative denials fire (the load-bearing test).** Under the **observer**
  identity (`MON.SVRCONN`), a destructive `MQGET` and an `MQPUT` against an audit
  ledger each return **MQRC 2035 (`MQRC_NOT_AUTHORIZED`)**. Under the
  **application** identity (`APP.SVRCONN`), an `MQOO_BROWSE` open of an audit
  ledger returns **2035**. These denials are the evidence that browse-only is now
  enforced by the queue manager, not by our code, and that the business identity
  can no longer read the history at all.
- **Boundary assertion.** The applied model touches only `GW.SEC.*` authrecs and
  our own observability role's identity config; the lab's channels/`CHLAUTH` are
  unchanged (declare-and-verify diff is clean).

Closes only on a recorded `Outcome: SUCCESS`.

## 8. Constraints & doctrine

- **Self-owning bolt-on rule.** §3. Own and automate every authrec; never mutate
  the lab's channels; declare-and-verify the identities.
- **Anonymization (absolute).** Generic identity/queue names only.
- **No silent failures.** A missing/unexpected effective identity, or a
  positive-path regression, is a loud stop — never a silent widening of a grant
  to "make it work".
- **MQ-native & minimalist.** `SET AUTHREC` via `runmqsc`; no new tooling, no AMS.

## 9. Open questions (resolve at pickup)

1. **Effective-identity discovery timing.** The non-privileged precondition
   itself is settled (§3, §7). The residual question is *mechanism/timing*: is
   `DISPLAY CHSTATUS … MCAUSER` on the live connection sufficient, or must we
   also read the `CHLAUTH SSLPEERMAP` rule to assert the mapping deterministically
   at **apply** time — before the collector has connected — so the authrec targets
   the right principal on a cold rebuild? The plan picks the discovery mechanism.
2. **Principal vs group.** Grant to the effective principal directly, or to a
   group it belongs to (MQ best practice favours groups)? Lab simplicity vs.
   real-world fidelity — decide in the plan.
3. **Alias authority checking.** Confirm whether `+put` must be granted on the
   `GW.SEC.INSTRUCT` alias, its resolved base `GW.SEC.INSTRUCT.AUDIT`, or both,
   under the QM's alias-resolution authority model.
4. **Inbound-MCA identity ownership.** The return channel is the lab's; confirm
   the MCA's effective identity and that granting it `+put` on our `GW.SEC.REPLY`
   stays within the boundary (we grant on our object; we do not touch the channel).

## 10. Sources (re-read directly before relying operationally)

- IBM MQ — `SET AUTHREC` (MQSC): `topic=commands-set-authrec`
- IBM MQ — authorities for MQI calls / object authorities:
  `topic=authorities-mqi-calls`
- IBM MQ — `DISPLAY CHSTATUS` (effective `MCAUSER`): `topic=commands-display-chstatus`
- IBM MQ — `CHLAUTH` / `SSLPEERMAP` channel-authentication records:
  `topic=channel-authentication-records`
- IBM MQ — reason code 2035 `MQRC_NOT_AUTHORIZED`: `topic=mqrc-2035`
