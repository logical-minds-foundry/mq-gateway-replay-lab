# Epic #235 — least-privilege MQ authority model — retrospective

*Partners `spec.md` and `plan.md`: what we set out to do, how we planned it, and
honestly how it went.*

---

## 0. At a glance

We set out to turn the streaming-audit design's headline property — a
**browse-only passive observer** — from a *code convention* into a
**queue-manager-enforced least-privilege authority**, proven with a negative
test. What shipped is exactly that: the gateway's audit history is now protected
by an OAM authority model on the live `NHAUAPP` queue manager — browse-only,
non-destructive, app-readable-own-history, no live-queue browsing — established
from a **pre-hardening** box, **mirroring the base lab's own authorization
model** rather than inventing a parallel one, and validated by a live **8/8
MQRC 2035 denial/permit matrix**.

The delta from plan was **large** — and productive. The epic was re-based once,
mid-flight, from "invent `SET AUTHREC` principals on our own objects" to "reuse
the base lab's already-defined `setmqaut -g` model and establish its Stage 2 on a
box that turned out never to have had it." Almost every material correction came
from **looking at the live/base-lab reality**, which the original spec had not.

**Work delivered:**

| PR | What it did |
|---|---|
| #246 | Spec + plan (initial model: per-identity principals via `SET AUTHREC`) |
| #248 | Revise spec + plan → **Option B**: separate *every* observer onto the `mqmon` identity; mirror the base lab |
| #250 | `gw-sec-observability`: connect the exporter as `mqmon` (`MON.SVRCONN` + `mq_prometheus`/OU=ops) |
| #251 | Wire `gw-sec-collector` into `site.yml` (committed deploy matches live) |
| #252 | `gw-sec-queues`: least-privilege grants via `setmqaut -g` + declare-verify Stage 2 |
| #253 | `gw-sec-svc-queues`: responder-side (`SVCQM`) grants |
| #254 | Docs: "Security & permissions" section (§13) + `ansible/README` pointer |
| #255 | Re-apply the cert-DN-alignment correction lost from the #248 merge |
| #257 | App reads its own history read-only; withhold browse on the real-time queues |
| **base lab #912** | Add an `authz` tag to scope the Stage-2 apply (`--tags authz`) |

Operational tasks (closed on recorded `Outcome: SUCCESS`, no PR): **#244** deploy
(Stage 2 + grants applied live), **#245** validate (the 2035 matrix).

- **Repos touched:** 2 — `mq-gateway-replay-lab` (9 PRs) and
  `mq-resiliency-lab-for-linux` (1 PR, the generic `authz` tag).
- **Tasks:** 13 children + the retrospective; all closed.
- **Releases:** none (lab-internal; no published artifact).
- **Span:** opened 2026-08-03, closed 2026-08-04 (this retrospective's merge).
- **Follow-ups filed:** `#258` (exporter/collector persistent deploy as `mqmon`,
  deferred), `vergil-project/vergil-tooling#2550` (identifier-consistency bug).

## 1. How the plan evolved

The plan did not survive contact with reality unchanged — and the four pivots are
the substance of this epic:

1. **Scope: collector-only → full model.** The design brief offered enforcing
   browse-only on the collector alone; the human chose the *full* model (app,
   observers, inbound MCA, responder). This set the ambition for everything after.

2. **Option B — separate the observer identity.** Writing the plan surfaced that
   the *committed* audit-queue reader (the `gw-sec-observability` exporter) browsed
   over `APP.SVRCONN` with the **business** cert — i.e. the observer and the
   application were the same effective principal — and that the streaming
   collector was **not in `site.yml`** at all. The clean model required moving
   *every* observer onto a dedicated read-only identity, which reshaped the spec
   and plan (#248).

3. **The re-base — mirror the base lab, and a pre-hardening box.** The largest
   deviation. A live query plus a study of the base lab found two things the
   original spec never knew: (a) the base lab **already defines** the complete
   least-privilege model — `mqapp`/`mqmon`/`mqsvc` service accounts, `CHLAUTH
   SSLPEERMAP` cert-DN→user maps, and `setmqaut -g` grants — wired into
   `site-nativeha-ubuntu.yml`; and (b) the running `NHAUAPP` box was
   **pre-hardening** (`MCAUSER(mqm)`, `CHLAUTH(DISABLED)`, the service accounts
   absent), so every process connected as the `mqm` super-user and "browse-only"
   was enforced *nowhere*. The epic re-based from *inventing* `SET AUTHREC`
   principals to *reusing* the base lab's `setmqaut -g` model and **establishing
   its Stage 2** on the box (#247/#248). The same investigation corrected the
   `+setall` model: the audit ledgers are QM-written via `STREAMQ`, so they need
   no client `+setall` — that concern lives only on the reply queue the return
   MCA writes.

4. **Two late design refinements.** After the model was live, the human made two
   calls the spec had gotten wrong: the audit ledgers are the **app's own
   history**, so `mqapp` should read them **read-only** (browse+inq), not be
   locked out (#257); and conversely, **browse should be withheld on the
   real-time `GW.SEC.REPLY`** — browsing a live in-flight queue is a performance
   anti-pattern (#257).

A fifth, smaller deviation: the cert-DN-alignment correction was **lost in a
merge** — PR #248 landed the revision at an earlier commit and dropped the
follow-up (`mon-client` → `mq_prometheus`), which the implemented code already
used; re-applied as #255 once the mismatch surfaced.

## 2. Lessons learned

- **Investigate the live and adjacent-repo reality *before* designing.** The
  biggest rework — the whole re-base — existed only because the first spec was
  written without checking the base lab or the running box. A single live
  `DISPLAY QMGR CHLAUTH` and a grep of the base lab's `authz.yml` up front would
  have produced the mirror-the-base-lab plan directly.
- **The pushback precondition earned its keep.** The "effective identities must
  be non-privileged" gate added during `paad:pushback` predicted the exact
  failure the live box embodied (`MCAUSER(mqm)` → OAM bypassed → grants inert). A
  spec-time skeptic caught a deploy-time reality.
- **Reuse beats reinvention — and the boundary rule permits it.** The corrected
  self-owning-bolt-on rule ("may change the underlying lab, if owned/automated/
  generic") is what let us *apply* the base lab's own Stage 2 and add a generic
  `authz` tag, rather than fork a parallel authorization mechanism.
- **Prove authority two ways.** `dspmqaut` enumeration (the grant surface) plus a
  live `2035` behavioral matrix (the identity mapping *and* the enforcement) is
  belt-and-suspenders; each catches what the other cannot.

## 3. Compromises & tradeoffs

- **Grants applied by hand, not via the role run (approach A).** The live apply
  used `setmqaut` executed directly over SSH — *verbatim from* the `gw-sec-queues`
  role, so the role remains the reproducible source of truth, but the role itself
  was not the thing that ran. Chosen because **this repo has no clean "apply
  against the live lab" runner** — a real gap (folded into #258). Reproducibility
  holds on a from-scratch rebuild via the role; the live convenience did not.
- **Exporter/collector services were *not* redeployed as `mqmon`.** #244's
  original acceptance included "exporter + collector run as `mqmon`"; that was
  **descoped to follow-up #258**. Justified: the services were not persistently
  installed anyway, the authority model is fully proven without them (the probe
  exercised the identities directly), and the base-lab `authrec` epic will
  reshape this area — deploying now would mean deploying twice.
- **`setmqaut -g` is a knowingly-temporary mechanism.** The base lab is moving to
  **`SET AUTHREC` by MQSC** (easier to manage remotely, no command-line
  arguments). This epic mirrored the *current* `setmqaut` convention; the bolt-on
  will need a follow-up re-sync to `authrec` once the base-lab epic lands. Debt
  taken deliberately to stay consistent with the base lab *as it is today*.
- **A non-issue-numbered branch existed transiently.** The base-lab `authz` tag
  was first created as `feature/authz-apply-tag` (no issue) via `worktree add
  -b`, which the naming guard — enforced only at commit time — did not block; it
  was renamed to `feature/911-authz-apply-tag` before any commit, so nothing
  improper merged. Surfaced the identifier-consistency gap logged below.

## 4. New problems & opportunities

- **#258 — exporter/collector persistent deploy as `mqmon`.** Filed under the
  ad-hoc epic, deferred behind the base-lab `authrec` epic. Its scope also carries
  the **"no repeatable ansible-apply runner for this repo against the live lab"**
  gap the deploy surfaced.
- **`vergil-project/vergil-tooling#2550` — identifier inconsistency.** One unit of
  work carried three different strings (worktree dir `authz-apply-tag`, branch
  `feature/911-authz-apply-tag`, finalize summary `authz-apply-tag`), and the
  branch-name issue-number guard fires at commit, not at branch creation. Logged
  for the tooling team.
- **Merge-loss edge on a post-`report-ready` correction.** The cert-DN fix added
  *after* the first `report-ready` did not make the merged PR (#248), forcing the
  re-apply in #255. Worth a caution: a correction folded in after report-ready
  needs the submit to pick up the *later* state, or it silently ships the earlier
  one.
- **Opportunity — the base lab's authz reuse pattern is now battle-tested.** This
  epic is a worked example of a bolt-on consuming the base lab's Stage-2 model +
  triggering it via a scoped tag; the pattern (and the `authz` tag) is reusable by
  any future bolt-on needing least privilege on the shared lab.

## 5. What's next

- **Base-lab authorization epic (`setmqaut` → `SET AUTHREC` by MQSC).** The
  human's next epic, deliberately on hold until this one landed to avoid chasing a
  moving target. When it lands it will reshape the identity/grant mechanism this
  epic mirrored.
- **Re-sync this repo to `authrec` + do #258 in the same pass.** After the base-lab
  epic, the bolt-on updates `gw-sec-queues`/`gw-sec-svc-queues` from `setmqaut -g`
  to the new `authrec` convention and finally deploys the exporter/collector as
  `mqmon` — one coherent re-sync rather than two.
- **Discretionary:** a one-line security cross-reference in
  `docs/proof-of-concept.md` (the top-level narrative omits the now-proven
  security dimension) — left to the author's framing, per the docs-review.

## Appendix A — Operational notes (the live Stage-2 + grant sequence)

The mechanical order that took `NHAUAPP` from pre-hardening to a proven
least-privilege posture:

1. **Base-lab enablement (merged, #912):** add `tags: [authz]` to the accounts
   play + active-instance discovery + the CHLAUTH/`setmqaut`/refresh tasks in
   `site-nativeha-ubuntu.yml`, so Stage 2 can be applied scoped. Verified with
   `--list-tasks --tags authz` (selects exactly those 8 tasks).
2. **Establish Stage 2 (live):**
   `ansible-playbook site-nativeha-ubuntu.yml --tags authz -e qm_app=NHAUAPP -e
   qm_svc=SVCQM -e chl_to_app=SVCQM.NHAUAPP`. **Gotcha:** `--tags authz` skips the
   plays that normally supply `qm_app`/`qm_svc`/`chl_to_app`, so they must be
   passed as extra-vars. Result: accounts on all 6 HA+DR nodes; `CHLAUTH(ENABLED)`
   + the three SSLPEERMAP maps + deny-all backstop; base grants.
3. **Cert-DN pre-check (why no lockout):** every live client cert already carried
   the OU the map keys off — `app-client` = `O=app-org,OU=apps` → `mqapp`,
   `mq_prometheus` = `O=app-org,OU=ops` → `mqmon`, `svc-responder` = `O=svc-org` →
   `mqsvc`. Confirmed against live `DISPLAY CHSTATUS … SSLPEER` before the flip.
4. **Our grants (`setmqaut -g`, verbatim from `gw-sec-queues`):** `mqmon`
   browse/inq/dsp on both audit ledgers; `mqapp` read-only browse/inq on both +
   the retained-original put on the outbound audit + get/inq on the reply (no
   browse); `mqsvc` put/setall on the reply. `dspmqaut`-verified.
5. **Proof (#245):** a pymqi probe (browse/get/put, asserting the reason code)
   over `APP.SVRCONN`/`MON.SVRCONN` presenting the client certs — 8/8: browse
   permitted read-only, `2035` on every destructive op and on the real-time-queue
   browse.

**Rollback (never needed):** `ALTER QMGR CHLAUTH(DISABLED)` reverts the flip
instantly; accounts and authrecs are additive.
