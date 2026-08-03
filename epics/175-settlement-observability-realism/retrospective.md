# Retrospective: Realistic SWIFT settlement traffic & signal-in-noise observability (epic #175)

> Partners `spec.md` and `plan.md` at the documentation tier. Read the three in
> order: **spec** (what we set out to do) → **plan** (how we planned it) →
> **retrospective** (honestly, how it went).

---

## §0 — At a glance

**What we set out to do.** Tune the SWIFT settlement collector's traffic and
dashboard from mechanism-demonstration toward **real-world realism**, so the board
delivers a **high signal-to-noise ratio** for detecting genuine settlement failures
**out-of-band** of the trading app — using only **macro, non-sensitive** dimensions.
Absorbed the dashboard/traffic-realism intent from the abandoned COA/COD epic (#105).

**What shipped.** All of it, and then the interesting part happened in the *live
deploy*. The 13-task, two-phase build landed almost exactly as planned — the design
pipeline (brainstorm → pushback → writing-plans → alignment) produced a code plan
that went essentially **13/13 green on first implementation** via parallel
sub-agents. The delta wasn't in the code; it was in **operational reality**: taking
this to the live lab caught **four real defects the box-only CI structurally cannot
see**, uncovered a **base-lab bring-up root cause**, and turned the Grafana renderer
into a **six-gap deployment odyssey** — all of which are the honest substance of this
record.

**Work delivered.** One repo (`logical-minds-foundry/mq-gateway-replay-lab`,
private/self-homing) + findings filed into the base lab
(`mq-resiliency-lab-for-linux`). Epic opened **2026-08-01**, closed **2026-08-03** —
~3 days. **~18 PRs** + 2 operational tasks (deploy, validate).

| Phase | PRs | What it delivered |
|---|---|---|
| Spec & plan | #186 | Epic spec + two-phase plan |
| Phase 1 — realism + honest board | #205–#211 | Calibration reference (sourced) · reason-family classifier · asset-class books + realistic mode · `inquire_depth` (MQINQ) · macro-segment accounting + oldest-age · segmented + audit-queue-health exposition · three-register board (Register A + C) + exporter config |
| Phase 2 — anomaly detection + catalog | #212–#217 | Anomaly primitives (binomial-z + EWMA) · scenario harness (7 episodes) · per-reason-family deviation scoring · Register B (deviation + rare-event amplification) · scenario-catalog runner · reusable observability-primitives note |
| Live-caught fixes | #219, #221, #224 | `inquire_depth` pymqi crash · ibmmq filesystem metric names · multi-target refId collision |
| Doc-review | #223 | Cross-reference the realism/signal-in-noise docs from the deliverable (§12.1) |
| Operational | #203, #204 | Deploy (collector + board + exporter config) · Validate (mechanism proven live) — closed on `Outcome:` |

**Test suite grew 423 → 518, 100% branch + line throughout, zero coverage omits.**

---

## §1 — How the plan evolved

The plan was a straight, well-formed line: 13 TDD tasks in two phases (Phase 1 =
realistic traffic + an honest three-register dashboard, standalone-valuable; Phase 2 =
anomaly detection + the scenario catalog). **That line shipped intact.** The
front-loaded design pipeline did its job — of the 13 implementation tasks driven by
parallel sub-agents, essentially all passed validation on the first attempt, and the
only *code* deltas were small, self-correcting ones the sub-agents caught mid-task
(two plan-snippet errors: a `Directive.outcome` field name and a determinism-breaking
variable shadow). A near-zero plan-vs-code delta: the discipline worked.

**The real delta lived one layer down, in the operational reality the plan could not
model** — and it is large:

**The live deploy caught four defects CI could not.** The repo maintains 100% branch
coverage by *mocking the MQ boundary* (a fake `pymqi`). That is necessary but not
sufficient, and the live deploy proved it: (1) `inquire_depth` called real pymqi's
`Queue.inquire()` with two attributes when it takes one per call — a crash the fake
surface hid (#219); (2) the board queried `ibmmq_qmgr_*_file_system_bytes_in_use` when
the real exporter emits `..._in_use_bytes` — a guess the T7 author had *flagged* as
unverified, now corrected (#221); (3) two board targets collided on `refId: A`, so the
disk-footprint panel rendered "No data" though the query returned data (#224). Plus
the deploy *mechanics* themselves (a root-owned venv needing `sudo pip`, stale
`__pycache__` from a crash-loop masking new code). **Four real bugs, all in the
boundary CI mocks — the single strongest argument for a live validation step.**

**The base lab was not where we left it.** The exporter that feeds Register C's
disk/log footprint was down. The root cause was not a crashed service but an
*incomplete bring-up*: `mqlab status` showed the stack's `observe` phase at `✗` — and
re-running bootstrap revealed `observe` actually *fails*, at the #877 renderer's
`configure` step, because the running obs box was **baked before #877 merged**. That
is a systemic gap (filed as base-lab **#886**): a change to a box's bake/install half
silently invalidates every pre-baked box, and nothing detects or enforces a rebake.

**The renderer became a six-gap odyssey.** Getting `grafana-image-renderer` working
on the live aarch64 box took six sequential fixes — none caught by CI, because the
box-bake path isn't exercised by static validation: snapd inactive for the arm64
chromium snap; Grafana refusing to boot with the default `renderer_token`; a missing
matching `AUTH_TOKEN` on the renderer (a 401); a wrong browser path; snap chromium
that *cannot run from a systemd service* (replaced with a non-snap Playwright
Chromium); and a missing `--disable-dev-shm-usage` for the VM. It now works — and that
matters, because dashboard-capture automation is something we will do a great deal of.

So: **spec → plan captured the destination and the code perfectly; the operational
terrain is where the epic taught us the most.** That is exactly the delta a
retrospective exists to record.

---

## §2 — Lessons learned

- **The four-stage design pipeline + parallel sub-agents is now proven at scale.**
  13 tasks, two phases, ~all green on first implementation, 100% coverage held
  throughout. Front-loaded judgment, trusted execution — it works, and the sub-agents
  even self-corrected the plan's own snippet errors.

- **Live validation catches what box-only CI structurally cannot — and it is
  load-bearing.** 100% branch coverage over a *mocked* MQ/exporter/Grafana boundary
  gave four green PRs that were each wrong against the real thing. The lesson is not
  "coverage is worthless" — it is "coverage of a mocked boundary is necessary but not
  sufficient; the live check is where boundary bugs die."

- **Minimal footprint is a feature, not a limitation.** The mid-design pivot to drop
  counterparty and instrument dimensions (macro-only: state/msg-type/reason-family/
  time) made the tool *both* less invasive *and* simpler — it dissolved the cardinality
  risk entirely. The best scope cut of the epic, and it strengthened the pitch.

- **Detect concentration, not volume.** Binomial-proportion z-score for spikes + EWMA
  for slow drift, over macro dimensions, made the rejection anomaly read **z ≈ +329
  bright red** against quiet green neighbours on live data. The signal-in-noise thesis
  held in reality, not just in tests.

- **Bake-time changes need cache invalidation (base lab).** #886: baked artifacts and
  the source that produced them are not tied together, so a bake-half change breaks
  `observe` on old boxes with a confusing error far from the cause.

---

## §3 — Compromises & tradeoffs

Load-bearing section — the debt knowingly taken.

- **The renderer works *live* via hot-patches, not in code.** Six fixes were applied
  directly on the obs box (AUTH_TOKEN, Playwright Chromium, browser path, dev-shm
  flag, the Grafana rendering drop-in). None are in the base-lab role — they are
  documented on base-lab **#886/#877** as the proper fix. **So auto-capture is
  reproducible only until the obs box is rebaked.** Real debt, consciously incurred to
  unblock the demo today.

- **The demo baseline is the messy prior-experiment backlog.** ~200k retained audit
  messages → 79.9% efficiency with *two* reason families red, not the cleaner "~97%
  green with a single injected anomaly" a stage demo wants. Deferred a clean-baseline
  reset **by owner decision** ("good enough; we only tune further if the audience
  engages").

- **The full per-scenario *live* catalog capture was deferred.** The 7 scenarios exist
  as committed, deterministic in-memory metric-dumps (T12), and the auto-capture
  pipeline is proven — but running each scenario against a clean live baseline and
  capturing its board snapshot is future tuning, not done here.

- **Deploy mechanics were hand-driven, not automated.** Because ansible wasn't on the
  session PATH initially (and to move fast on a degraded lab), the collector redeploy
  was manual (build wheel → `sudo pip` → clear pycache → restart) rather than via the
  `gw-sec-collector` role. It worked and is documented (Appendix A), but it's a
  one-off, not the reproducible role path.

- **This epic absorbed a dead end.** COA/COD (#105) proved incompatible with the
  streaming-queue mechanism (it needs app-side report options that violate the
  transparency invariant); #175 took over its dashboard/realism intent. Context, not
  this epic's debt, but it explains why #175 exists.

---

## §4 — New problems & opportunities

- **Base-lab #886 — baked-box cache invalidation.** *Filed.* The systemic bug the
  renderer exposed: bake-half changes don't invalidate pre-baked boxes, and `observe`
  fails deep instead of flagging "rebake required." Design directions captured
  (recipe-hash manifest; resilient configure halves; a CI gate on bake-half changes).

- **Base-lab #877 renderer needs real deployment testing.** *Logged on #886.* Six
  integration gaps (arm64 chromium/snap, `renderer_token`, `AUTH_TOKEN`, browser path,
  snap-in-service, dev-shm) that merged green but don't produce a working renderer.
  The live hot-patches are the reference for the proper role fix.

- **The `observe`-phase-never-completed bring-up gap.** A stack can sit at
  `provision ✓ / observe ✗` — provisioned but with no observability — and downstream
  experiments hit a silently-degraded lab. Worth a bring-up-completeness guard.

- **Demo-grade dashboard tuning.** *Deferred by design.* A clean-baseline realistic
  load + the full 7-snapshot live catalog, done only if the audience engages.

---

## §5 — What's next

- **Immediate (owner, out-of-band):** the presentation/deliverable write-up — the
  benefits of the streaming-queue audit mechanism, the scale/performance evidence
  (the traffic + SWIFT-message simulators, disk footprint, IO overhead), with a
  dashboard screenshot as the drip-fed eye-candy. Deliberately *outside* the source
  tree.

- **The real gate (owner, at work):** the **MQMD-fidelity analysis** — confirming the
  message-descriptor fields altered when a message is streamed do not violate the
  production system's requirements. This, not the dashboard, decides whether the whole
  approach is viable; a showstopper here (unlikely-to-be-negotiable vendor behaviour)
  would end it.

- **Base lab:** fix #886 (cache invalidation) and the #877 renderer properly, so
  auto-capture survives a rebake.

- **This lab:** demo-grade dashboard tuning if there is audience interest.

---

## Appendix A — Operational notes (the live deploy sequence)

Access: lab VMs are `qemu:///system` libvirt/vagrant guests; ssh as `vagrant` with
`~/.vagrant.d/insecure_private_key` (host-key checking off — keys churn on rebuild);
drive the lab via `mqlab` in the base-lab `.venv`. Node map:
NHAUAPP a1/a2/a3 = `.11/.12/.13`, app-client (collector) = `.60`, mon-probe
(exporters) = `.3`, obs (Grafana/Prometheus) = `.2`.

1. **Collector** (app-client `.60`): build the wheel (`uv build`), `scp` with its
   PEP-427 name, `sudo /home/vagrant/mqvenv/bin/pip install --force-reinstall --no-deps`
   (venv is **root-owned** → needs `sudo`), **clear `__pycache__`** (stale bytecode
   from a crash-loop masked the new code), restart `gwlab-collector`. Verified: new
   metrics publish, Prometheus scrapes, deviation live.
2. **Board**: `build_settlement_board` → Grafana API upsert (`overwrite`).
3. **Base-lab exporter restore**: root cause `observe ✗`; brought up the
   `mq_prometheus` exporters with `site-obs.yml --limit probe` (skips the failing
   renderer play). NHAUAPP `:9163` / SVCQM `:9158` up, filesystem series flowing.
4. **Renderer (six gaps)**: snapd for the arm64 chromium; Grafana `renderer_token`;
   matching renderer `AUTH_TOKEN`; non-snap Playwright Chromium (snap can't run from a
   service); `--disable-dev-shm-usage`. Then the render API produces PNGs.

Gotchas hit: zsh doesn't word-split unquoted vars (inline ssh options); heredocs
blocked (write files); renamed wheels rejected by pip (keep the PEP-427 name);
host-key churn (`UserKnownHostsFile=/dev/null`).

## Appendix B — Extended metrics

- **Tests:** 423 → 518, 100% branch + line throughout, zero coverage omits.
- **Live board:** settlement efficiency 79.9%; Register B deviation — cash −31.7,
  matching −63.9, **rejection +329 (red)**, settlement +52.4; audit disk/log footprint
  ~6.88 GB per filesystem; audit-queue depths INSTRUCT ≈ 200k / STATUS ≈ 548k; oldest
  message ≈ 2.6-day window.
- **Live-caught defects:** 4 (three merged fixes #219/#221/#224 + the deploy
  mechanics). **Base-lab issues filed:** 1 (#886) + the #877 renderer follow-on.
