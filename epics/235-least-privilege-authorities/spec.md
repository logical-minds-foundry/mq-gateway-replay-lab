# Epic #235 — least-privilege MQ authority model — spec

*A generic trading back-office gateway problem. No employer/client/vendor
identifiable content; invented queue/host/identity names only.*

> **Revision (this epic, task #247).** The original spec/plan (merged in #246)
> invented per-identity principals and `SET AUTHREC` grants on our own objects,
> under the assumption that the channels' identities were the base lab's to
> declare-and-verify but not to change. A live investigation and a study of the
> base lab changed that: the base lab **already defines** the complete
> least-privilege model — service accounts, `CHLAUTH SSLPEERMAP` identity maps,
> and `setmqaut -g` group grants — and the corrected self-owning-bolt-on rule
> lets us apply it. This revision re-bases the epic to **mirror and reuse** that
> defined model rather than reinvent a parallel one.

---

## 1. Origin — why this epic exists

The streaming-audit design's headline property is a **browse-only passive
observer**: many independent tools can read the rolling history at once, none of
them able to alter or consume it, while exactly one application owns the
transactional processing. That guarantee is asserted in the presentation
material and the design docs.

**Live state, queried directly against `NHAUAPP` (data, this session):**

- The only authority record on **either** audit queue (`GW.SEC.INSTRUCT.AUDIT`,
  `GW.SEC.STATUS.AUDIT`) is the **auto-created `mqm`-group full-control default**
  (`BROWSE,CHG,CLR,DLT,DSP,GET,INQ,PUT,PASSALL,PASSID,SET,SETALL,SETID`). We grant
  nothing. There is no application/observer record and no generic/wildcard profile.
- Both channels are pinned to `MCAUSER(mqm)` and the QM has **`CHLAUTH(DISABLED)`
  / `CONNAUTH( )`** — so every process (app, exporter, collector) connects as the
  **`mqm` super-user**, for which OAM checks are bypassed entirely. "Browse-only"
  is enforced *only* by reader code; at the queue-manager level the readers hold
  full destructive rights over the audit history.
- The base lab's service accounts `mqapp`/`mqmon`/`mqsvc` **do not exist** on the
  box.

**What this means:** the box is **pre-hardening**. The base lab has since built a
complete least-privilege authorization model (Stage 2 authz — service accounts +
`CHLAUTH SSLPEERMAP` + `setmqaut -g`, epic #619, wired into
`site-nativeha-ubuntu.yml`), but `NHAUAPP` was provisioned before it merged and
has not been re-provisioned since. So there are **two** gaps: (a) the base lab's
model is **not applied** to this box, and (b) we have added **no grants** for our
own `GW.SEC.*` queues on top of it.

This epic closes both — by **applying the base lab's already-defined model** to
`NHAUAPP` and **layering our own least-privilege grants** for our queues on top —
making browse-only an enforced queue-manager boundary, proven with a negative test.

It is **largely academic by design**: the deliverable is a correct reference model
demonstrated on the objects we own, not a fix for a live incident.

## 2. Mission

**Reuse, don't reinvent.** Adopt the base lab's defined least-privilege model for
the settlement gateway — its `mqapp` (application), `mqmon` (monitoring,
read-only), and `mqsvc` (QM-to-QM receiver MCA) group identities, its
`CHLAUTH SSLPEERMAP` cert-DN→user mapping, and its `setmqaut -g` grant mechanism —
and **layer our own `GW.SEC.*` grants** on top so each identity holds only what
its role needs. Prove it live, including the denial paths. As part of this, the
**observability tooling connects as the read-only `mqmon` identity** (over
`MON.SVRCONN`), never the business identity; the app (`mqapp`) additionally holds
**read-only** access to its *own* history on the audit ledgers. No identity but the
admin can consume or mutate the ledgers.

## 3. The boundary principle (corrected self-owning bolt-on rule)

The identities reach the queue manager over the base lab's channels
(`APP.SVRCONN` → `mqapp`, `MON.SVRCONN` → `mqmon`, the inter-QM return channel →
`mqsvc`). Under the **corrected** self-owning-bolt-on rule (CLAUDE.md, #234) this
repo **may change the underlying lab whatever it needs to**, provided it (a)
automates the change 100%, (b) owns it so it replays on a from-scratch rebuild,
and (c) keeps changes that live in the base lab **generic**. The split for this
epic:

- **The channel-identity hardening is the base lab's, already generic and
  defined.** We do **not** invent `CHLAUTH`/accounts. We **declare-and-verify**
  the base lab's Stage 2 is present on `NHAUAPP` (accounts exist, `CHLAUTH(ENABLED)`,
  the `SSLPEERMAP` maps resolve `APP.SVRCONN`→`mqapp` / `MON.SVRCONN`→`mqmon`) and,
  because it is currently **absent**, **apply the base lab's own generic
  playbook** (`site-nativeha-ubuntu.yml` authz) to establish it — an owned,
  automated, reproducible prerequisite (§6.0). Nothing bolt-on-specific is added
  to the base lab.
- **Our own delta is the `GW.SEC.*` grants.** `setmqaut -g` for the base lab's
  existing groups on the queues this repo owns — 100% owned and automated by our
  Ansible, replaying on rebuild.

This keeps the base lab generic (it never learns about `GW.SEC.*`) while making
the model real on our objects.

**Load-bearing precondition — the effective identities must be the non-privileged
Stage-2 accounts.** OAM checks are bypassed for `mqm`. The proof in §7 is
possible **only** once the channels resolve to `mqapp`/`mqmon` (not `mqm`) — i.e.
once Stage 2 is applied. §6.0 makes establishing Stage 2 a hard prerequisite; the
validation (§7) asserts it before trusting any grant, and a residual `mqm`
identity is a **loud stop**, never a silent widening.

**Cert-DN alignment — verified this session (no lockout risk).** Enabling
`CHLAUTH` only maps our connections correctly if our certs carry the OUs the
`SSLPEERMAP` keys off. Measured directly: `app-client` = `O=app-org, OU=apps` →
`mqapp`; `mq_prometheus` = `O=app-org, OU=ops` → `mqmon`; `svc-responder` =
`O=svc-org, OU=messaging` → `mqsvc`. All three match the base lab's maps, so no
identity hits the deny-all backstop when Stage 2 is applied. The observers must
present the `mq_prometheus`/OU=ops keystore (the sole such cert on the box);
there is **no distinct `mon-client` cert** — that name was only a placeholder.

## 4. The topology the model must fit

```
                 application (APP.SVRCONN → mqapp)
                   │  MQPUT                         ▲  MQGET
                   ▼                                │
        GW.SEC.INSTRUCT (QALIAS) ───► GW.SEC.INSTRUCT.AUDIT   GW.SEC.REPLY
                                        │ (retained original)   ▲
                                        │ STREAMQ MUSTDUP        │ return MCA → mqsvc
                                        ▼                        │ (inter-QM channel)
                                   GW.SEC.INSTRUCT.REMOTE ──► counterparty ──┘
                                                                 │ STREAMQ MUSTDUP
                                   GW.SEC.STATUS.AUDIT ◄─────────┘  (from GW.SEC.REPLY)

        observers (MON.SVRCONN → mqmon) ── browse only ──► GW.SEC.INSTRUCT.AUDIT
          • gw-sec-collector  (streaming, stateful)          GW.SEC.STATUS.AUDIT
          • gw-sec-observability exporter (per-scrape)
```

Two crux points:

1. **The audit ledgers are written by the queue manager's STREAMQ engine, under
   the QM's own authority.** The only client write is the app's retained-original
   `+put` to the outbound audit; there is **no client `+get`/`+setall`** on them.
   (This corrects the original spec, which granted an inbound MCA `+put +setall` on
   an audit queue.) The `+setall` context-authority concern lives on
   **`GW.SEC.REPLY`**, which the inter-QM **return MCA** genuinely writes.
2. **The audit ledgers are the application's own history, readable read-only by
   more than one identity.** `mqapp` *puts* the retained original to
   `GW.SEC.INSTRUCT.AUDIT` and may *browse* both ledgers to view its own history;
   `mqmon` *browses* both for out-of-band monitoring. **Neither may `GET`** (which
   would consume the retained original) or otherwise mutate them. The collector is
   merely one example of the passive readers the ledgers enable; the app reading its
   own history is another. A message leaves an audit ledger only by expiry
   (`CAPEXPRY`); `CLEAR`/`DELETE` is a separate privileged administrative act
   (`mqm`).

## 5. The full least-privilege model (target end-state)

The base lab establishes the identities and channel maps (§6.0). This epic adds,
via **`setmqaut -g`** (groups, matching the base lab — never principals), the
grants on the objects `gw-sec-queues`/`gw-sec-svc-queues` own:

| Group (via) | Object | Authorities | Explicitly NOT |
|---|---|---|---|
| `mqapp` (`APP.SVRCONN`) | `GW.SEC.INSTRUCT` alias → `GW.SEC.INSTRUCT.AUDIT` | `+put +browse +inq` | `+get +set +dlt +clr` |
| `mqapp` (`APP.SVRCONN`) | `GW.SEC.STATUS.AUDIT` | `+browse +inq` | `+get +put +set +dlt +clr` |
| `mqapp` (`APP.SVRCONN`) | `GW.SEC.REPLY` (real-time) | `+get +inq` | `+browse` (anti-pattern on a live queue) |
| `mqmon` (`MON.SVRCONN`) — collector **and** exporter | `GW.SEC.INSTRUCT.AUDIT`, `GW.SEC.STATUS.AUDIT` | `+browse +inq +dsp` | `+get +put +set +dlt +clr` |
| `mqsvc` (return MCA) | `GW.SEC.REPLY` | `+put +setall` | anything on the audit ledgers |

Both `mqapp` and `mqmon` read the audit ledgers **read-only** (browse/inq); the
app additionally holds `+put` on the outbound audit for the retained original.
The app's read-only access to its own history is a deliberate design capability —
the ledgers serve out-of-band monitoring *and* the app viewing its own history —
even though the current implementation does not exercise the app-read path.

Grants follow the base lab's `authz.yml` shape (`{ group, object_type, object,
authorities }`) and its `qmgr +connect` grants (already established by Stage 2 for
`mqapp`/`mqmon`). Names are the base lab's (≤ 12 chars, group-scoped).

For the objects `gw-sec-svc-queues` owns on `SVCQM` (the responder side): the
responder's own least-privilege grants on its landing/reply objects, using
whatever Stage-2 identity `SVC.SVRCONN` resolves to on `SVCQM` (declare-and-verify;
exact grants enumerated in the plan).

Model-level invariants:

- **`setmqaut -g` to groups, matching the base lab.** Under OS-based OAM the
  effective `MCAUSER` (`mqapp`/`mqmon`) is a real OS principal whose primary group
  carries the authority; granting to the group is the base lab's convention and the
  portable choice across failover nodes.
- **Audit ledgers are read-mostly, non-destructively.** The only client write is
  the app's retained-original `+put` to the outbound audit; both `mqapp` and `mqmon`
  otherwise hold **read-only** (`+browse +inq`, plus `+dsp` for `mqmon`). No client
  holds `+get`/`+set`/`+clr`/`+dlt` on either ledger. `+setall` is confined to
  `mqsvc` on `GW.SEC.REPLY`, per the base lab's context-authority finding (§10).
- **Browse belongs on the audit ledgers, never the real-time queues.** The audit
  ledgers exist to be read (`+browse`), and their integrity is protected precisely
  because **no one can `GET`** them — a message leaves only by expiry (`CAPEXPRY`).
  The request/reply queues (`GW.SEC.INSTRUCT`, `GW.SEC.REPLY`) are the opposite: a
  live get/put flow, where **`+browse` is withheld** — browsing an in-flight queue
  meant for real-time processing is a performance anti-pattern. `+inq` (attribute
  query) is fine on either; `+browse` is not granted on the real-time queues.
- **Authorities are per-operation.** `+put`/`+browse` on an audit ledger does **not**
  confer `+get`; the validation confirms neither `mqapp` nor `mqmon` holds `+get` on
  either audit ledger from any source — the load-bearing non-destructive invariant.
- **`+connect`** is a base-lab Stage-2 grant for `mqapp`/`mqmon`; we
  declare-and-verify it, we do not add it.
- **Administrative cleanup** (`CLEAR`, `DELETE`) stays with `mqm`; never `mqmon`
  or `mqapp`.

## 6. Deliverables

**6.0 — Establish the base lab's Stage 2 on `NHAUAPP` (owned prerequisite).**
The box is pre-hardening (§1). Apply the base lab's **already-defined, generic**
authorization (`site-nativeha-ubuntu.yml` authz: `mq-authz-accounts` +
`authz.mqsc.j2` CHLAUTH maps + its `setmqaut -g` base grants) so `mqapp`/`mqmon`/
`mqsvc` exist, `CHLAUTH(ENABLED)`, and the `SSLPEERMAP` maps resolve the channels
to the non-privileged accounts. Owned and automated from this repo (invoking the
base lab's playbook), declare-and-verified, reproducible on rebuild. **Nothing
bolt-on-specific is added to the base lab.**

1. **Observer-identity separation (`gw-sec-observability`)** — switch the exporter
   from `APP.SVRCONN` + the `app-client` cert (`OU=apps` → `mqapp`) to
   `MON.SVRCONN` + the `mq_prometheus` cert (`OU=ops` → `mqmon`), so every
   audit-queue reader is the read-only `mqmon` identity. (Cert DNs verified this
   session — see §3; `mq_prometheus`/OU=ops is the base lab's monitoring identity
   and the only such keystore on the box; "mon-client" was a naming placeholder.)
2. **Wire the collector into `site.yml` (`gw-sec-collector`)** — so the committed
   deploy matches what runs live (hot-deployed during epic #175).
3. **`gw-sec-queues`** — declare-and-verify Stage 2 is present, then `setmqaut -g`
   the §5 grants for `mqmon`/`mqapp` on our queues; verify `mqmon` holds no
   get/put on the audit ledgers and `mqapp` is read-only there (may browse; no get).
4. **`gw-sec-svc-queues`** — the `mqsvc` grant on `GW.SEC.REPLY` (`+put +setall`)
   and the responder-side grants on `SVCQM`.
5. **Docs** — a "Security & permissions" section in the settlement-tracking setup
   doc (and an `ansible/README` pointer) documenting the model, the reuse of the
   base lab's identities, and the ownership boundary; cross-reference the base
   lab's MQ authorization guide.
6. **Live validation task** — see §7.

Mirror the base lab's `group_vars/all/authz.yml` **data-structure pattern** for
our grant map, and its `site-nativeha-ubuntu-authz-validate.yml` **harness
pattern** for §7.

## 7. Validation — the proof is the denial

A `validation`-kind operational task, run after 6.0 and the role changes are
applied to the live lab.

**Precondition self-check (run first; blocks the task if unmet).** Assert Stage 2
is present on `NHAUAPP`: `mqapp`/`mqmon`/`mqsvc` exist, `CHLAUTH(ENABLED)`, and the
channels resolve to the non-privileged accounts (`DISPLAY CHLAUTH` / `DISPLAY
CHSTATUS … MCAUSER` shows `mqapp`/`mqmon`, **not** `mqm`). If any channel still
resolves to `mqm`, comment "blocked: preconditions not met — Stage 2 absent;
<channel> resolves to mqm" and **stop** — never fabricate a pass.

With the precondition met:

- **Positive paths still work.** App (`mqapp`) puts an instruction; it streams to
  the counterparty and is retained; a reply lands on `GW.SEC.REPLY` (written by the
  `mqsvc` return MCA) and streams to `GW.SEC.STATUS.AUDIT`; **both** observers on
  `MON.SVRCONN` (`mqmon`) browse both audit ledgers and their metrics advance. The
  app (`mqapp`) can also `MQOO_BROWSE` both ledgers read-only — its own history — and
  succeeds. No regression from the new grants or the exporter identity switch.
- **Negative denials fire (the load-bearing test).** Under **both** `mqmon` and
  `mqapp`, a destructive `MQGET` against an audit ledger returns **MQRC 2035
  (`MQRC_NOT_AUTHORIZED`)**, as does an `MQPUT` under `mqmon` (and any `MQPUT` under
  `mqapp` on the QM-written `GW.SEC.STATUS.AUDIT`). These denials are the evidence
  that access is **read-only, enforced by the queue manager** — no identity but the
  admin (`mqm`) can consume or mutate the history.
- **No-wider-grant enumeration.** `DISPLAY AUTHREC` / `dmpmqaut` on each audit
  ledger confirms **no** group (beyond the `mqm` admin default) holds `+get` — only
  `mqmon` and `mqapp` hold read-only `+browse +inq` (plus the app's retained-original
  `+put` on the outbound audit).
- **Boundary assertion.** Stage 2 came from the base lab's own generic playbook; our
  added change is only the `GW.SEC.*` `setmqaut -g` grants + our observability
  role's identity config. The base lab carries no `GW.SEC.*` knowledge.

Closes only on a recorded `Outcome: SUCCESS`.

## 8. Constraints & doctrine

- **Self-owning bolt-on rule (corrected).** §3. Own and automate every change,
  including applying the base lab's Stage 2 to `NHAUAPP`; keep base-lab changes
  generic; declare-and-verify before relying.
- **Reuse the base lab's model, don't fork it.** Same accounts (`mqapp`/`mqmon`/
  `mqsvc`), same `setmqaut -g` mechanism, same `authz.yml`/harness patterns.
- **Anonymization (absolute).** Generic identity/queue names only.
- **No silent failures.** A residual `mqm` identity, a missing account, or a
  positive-path regression is a loud stop.
- **MQ-native & minimalist.** `setmqaut` / `runmqsc`; no new tooling, no AMS.

## 9. Open questions (resolve at pickup)

1. **How to invoke the base lab's Stage 2 from here (§6.0).** Run
   `site-nativeha-ubuntu.yml` (whole arm) vs a targeted authz play/tag; how our
   Ansible/`gwlab` owns that invocation reproducibly. The plan picks the mechanism.
2. **`SVC.SVRCONN` identity on `SVCQM`.** Confirm the responder's Stage-2 effective
   identity on the counterparty QM and its least-privilege grants (declare-and-verify).
3. **Alias authority checking.** Confirm whether `mqapp`'s `+put` binds on the
   `GW.SEC.INSTRUCT` alias, its resolved base `GW.SEC.INSTRUCT.AUDIT`, or both.
4. **`gwlab`/data-pattern shape.** Whether to vendor the base lab's `authz.yml`
   data-structure and validate harness into our Ansible, or reference them; keep
   base-lab assets generic either way.

*(Resolved by this revision: the earlier "principal vs group" and "effective-
identity discovery" questions — we adopt the base lab's **groups** and its
**SSLPEERMAP** identities, declare-and-verified.)*

## 10. Sources (re-read directly before relying operationally)

- **Base lab — MQ authorization guide:**
  `mq-resiliency-lab-for-linux/docs/site/docs/guides/mq-authorization-guide.md`
  (the defined least-privilege model this epic mirrors).
- **Base lab — Stage 2 authz assets:** `ansible/roles/mq-authz-accounts/`,
  `ansible/roles/mq-pcmk-qmgr/templates/authz.mqsc.j2`,
  `ansible/group_vars/all/authz.yml`, `ansible/site-nativeha-ubuntu.yml` (authz
  wiring), `ansible/site-nativeha-ubuntu-authz-validate.yml` (harness).
- **Base lab — context authority report** (why only `mqsvc` gets `+setall`, and
  why the audit ledgers do not):
  `docs/reports/2026-07-13-mq-putaut-ctx-setall-context-authority.md`.
- IBM MQ — `setmqaut` / object authorities: `topic=authorities-mqi-calls`.
- IBM MQ — `CHLAUTH` / `SSLPEERMAP`: `topic=channel-authentication-records`.
- IBM MQ — reason code 2035 `MQRC_NOT_AUTHORIZED`: `topic=mqrc-2035`.
