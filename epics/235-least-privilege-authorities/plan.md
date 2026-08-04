# Least-privilege MQ authority model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revision (task #247).** Re-based to **mirror and reuse the base lab's defined
> authorization model** (service accounts `mqapp`/`mqmon`/`mqsvc`, `CHLAUTH
> SSLPEERMAP` identity maps, `setmqaut -g` group grants) instead of inventing
> principals and `SET AUTHREC`. The running `NHAUAPP` box is pre-hardening, so
> establishing the base lab's Stage 2 on it is an owned prerequisite (§6.0).

**Goal:** Make browse-only an enforced queue-manager boundary for the settlement
audit history by adopting the base lab's least-privilege identities and layering
our own `setmqaut -g` grants on the `GW.SEC.*` queues, proven live with a
2035-denial negative test.

**Architecture:** Infrastructure + operational. The base lab **already defines**
the identity model; we (0) apply its Stage 2 to the pre-hardening `NHAUAPP` box
via the base lab's own generic playbook, then (1–5) point our observer at the
`mqmon` identity, wire the collector into the committed deploy, add `setmqaut -g`
grants for the base lab's groups on our queues, and document it — then prove it
live. No Python logic changes; the readers already open `MQOO_BROWSE`.

**Tech Stack:** Ansible (roles + `site.yml`), `setmqaut -g`, `runmqsc`
(`DISPLAY CHLAUTH`/`CHSTATUS`/`AUTHREC`), IBM MQ OAM + `CHLAUTH SSLPEERMAP`,
GSKit, the base lab's `mq-authz-accounts` role + `authz.mqsc.j2` + `authz.yml`.

## Global Constraints

- **Self-owning bolt-on rule (corrected).** Own and automate every change,
  including applying the base lab's Stage 2 to `NHAUAPP`. Keep base-lab changes
  **generic** — never encode `GW.SEC.*` into the base lab. Declare-and-verify
  before relying; a residual `mqm` identity is a loud stop.
- **Reuse the base lab's model.** Identities `mqapp` (app), `mqmon` (monitoring,
  read-only), `mqsvc` (return MCA). Grants via **`setmqaut -g` to groups**, never
  principals, never `SET AUTHREC`. Names ≤ 12 chars.
- **Audit ledgers are QM-written (STREAMQ).** No client `+put`/`+setall` on them;
  only `mqmon +browse +inq +dsp`. `+setall` is confined to `mqsvc` on
  `GW.SEC.REPLY`.
- **Anonymization (absolute).** Generic identity/queue/host names only.
- **Cert-DN alignment (verified this session — no lockout risk).** The existing
  certs already carry the OUs the Stage-2 `SSLPEERMAP` keys off: `app-client` =
  `O=app-org,OU=apps` → `mqapp`; `mq_prometheus` = `O=app-org,OU=ops` → `mqmon`;
  `svc-responder` = `O=svc-org` → `mqsvc`. Enabling `CHLAUTH` maps them cleanly;
  none hit the deny-all backstop. Observers must present the `mq_prometheus`/OU=ops
  keystore (the only such cert on the box); "mon-client" was a naming placeholder.
- **Validation command (the only one):** `vrg-container-run -- vrg-validate`.

---

## File Structure

- `ansible/roles/gw-sec-observability/defaults/main.yml` + `tasks/main.yml` —
  retarget exporter identity to `MON.SVRCONN` + the `mq_prometheus`/OU=ops
  keystore (→ `mqmon`) (Task 1).
- `ansible/site.yml` — add the `gw-sec-collector` play (Task 2).
- `ansible/roles/gw-sec-queues/defaults/main.yml` + `tasks/main.yml` — the grant
  map + declare-and-verify Stage 2 + `setmqaut -g` loop (Task 3).
- `ansible/roles/gw-sec-svc-queues/defaults/main.yml` + `tasks/main.yml` —
  `mqsvc`/responder grants (Task 4).
- `docs/mq-native-settlement-tracking.md` + `ansible/README.md` — docs (Task 5).
- **Base lab (declare-and-verify + apply, not modify):**
  `mq-resiliency-lab-for-linux/ansible/site-nativeha-ubuntu.yml` (authz),
  `roles/mq-authz-accounts/`, `templates/authz.mqsc.j2`, `group_vars/all/authz.yml`.

Tasks 1–5 are separate GitHub issues, each closed by its own PR. The **deployment**
task (§6.0 apply + our roles) is blocked-by 1–4; the **validation** task is
blocked-by the deployment.

---

## Task 1: Observer identity — exporter → `MON.SVRCONN` (`mq_prometheus`/OU=ops → `mqmon`)

**Files:** Modify `ansible/roles/gw-sec-observability/defaults/main.yml` +
`tasks/main.yml`.

**Interfaces:** Produces an exporter that connects over `MON.SVRCONN` — which
Stage 2 maps to `mqmon`, the read-only identity Task 3 grants `+browse` on the
audit ledgers. After this, no audit-queue reader connects as `mqapp`.

- [ ] **Step 1: Read the keystore-handling blocks** in
  `gw-sec-observability/tasks/main.yml` and the monitoring-keystore equivalents in
  `gw-sec-collector/tasks/deploy.yml`.

- [ ] **Step 2: Retarget the identity defaults** in
  `ansible/roles/gw-sec-observability/defaults/main.yml`:

```yaml
gw_sec_exporter_channel: MON.SVRCONN            # was APP.SVRCONN (Stage 2 SSLPEERMAP maps this to mqmon)
gw_key_repo: /var/mqm/ssl/mq_prometheus/key     # the OU=ops monitoring cert; was /var/mqm/ssl/app-client/key
gw_cert_label: mq_prometheus                    # the mqmon identity's cert (verified DN O=app-org,OU=ops); was app-client
```

**Identity note (cert-DN verified, this session).** The observers' cert must
present **`O=app-org, OU=ops`** — the base lab's `MON.SVRCONN` `SSLPEERMAP` maps
that DN to `mqmon`. The one such cert on the box is **`mq_prometheus`** (subject
`O=app-org, OU=ops, CN=mq_prometheus`); there is **no separate `mon-client`
cert** — it was only ever a naming placeholder. Point the exporter (and the
collector) at the `mq_prometheus`/OU=ops keystore. (Confirmed aligned:
`app-client` = `OU=apps` → `mqapp`; `mq_prometheus` = `OU=ops` → `mqmon`.)

Add the monitoring-keystore apply-time vars (mirroring the collector's; the
keystore supplied is the base lab's `mq_prometheus` / OU=ops keystore):

```yaml
# The exporter now connects as the mqmon observer identity (via MON.SVRCONN),
# NOT the mqapp business identity (epic #235). The supplied keystore is the base
# lab's mq_prometheus (O=app-org,OU=ops) cert — the identity the SSLPEERMAP maps
# to mqmon. REQUIRED at apply time.
gw_sec_exporter_mon_p12: ""   # REQUIRED — path to the mq_prometheus (OU=ops) keystore on the controller
gw_sec_exporter_mon_pw: ""    # REQUIRED — the monitoring keystore password
```

- [ ] **Step 3: Point the keystore tasks at the mq_prometheus vars** in
  `tasks/main.yml` (assert preconditions + distribute + `runmqakm` stash consume
  `gw_sec_exporter_mon_p12`/`_pw`; rename comments app-client → mq_prometheus/OU=ops).

- [ ] **Step 4: Grep for lingering app-client references**

```bash
grep -rnE 'app.client|APP\.SVRCONN|app_client' ansible/roles/gw-sec-observability/
```
Expected: no matches.

- [ ] **Step 5: Validate** — `vrg-container-run -- vrg-validate` → PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope gw-sec-observability \
  --message "connect the settlement exporter as the mqmon observer identity" \
  --body "Epic #235: the exporter uses MON.SVRCONN + the mq_prometheus (O=app-org,OU=ops) keystore, which the Stage-2 SSLPEERMAP maps to the read-only mqmon account — not APP.SVRCONN/app-client (OU=apps → mqapp) — so no audit-queue reader connects as the business identity."
```

---

## Task 2: Wire `gw-sec-collector` into `site.yml`

**Files:** Modify `ansible/site.yml`.

**Interfaces:** A committed deploy that includes the streaming collector, so a
from-scratch rebuild reproduces the `mqmon` observer.

- [ ] **Step 1: Read the two `hosts: gateway_obs` plays** at the tail of
  `ansible/site.yml`.

- [ ] **Step 2: Append the collector play**

```yaml
# Deploy the streaming settlement collector: the box-only stateful reader that
# browses both GW.SEC.* audit queues as the mqmon observer identity (MON.SVRCONN)
# and publishes derived metrics to Prometheus (epic #65). Wired here so the
# committed deploy matches live (epic #235). Owns only /opt/gwlab + its own unit
# + its own monitoring keystore.
- name: Deploy the gateway SWIFT settlement collector (streaming, box-only)
  hosts: gateway_obs
  gather_facts: false
  roles:
    - gw-sec-collector
```

- [ ] **Step 3: Syntax-check** —
  `vrg-container-run -- ansible-playbook ansible/site.yml -i ansible/inventory/lab.yml --syntax-check` → PASS.

- [ ] **Step 4: Confirm enumerated** —
  `... --list-tasks 2>&1 | grep -i collector` shows the role's tasks.

- [ ] **Step 5: Validate** — `vrg-container-run -- vrg-validate` → PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope ansible \
  --message "wire gw-sec-collector into site.yml so the committed deploy matches live" \
  --body "Epic #235: add the collector play on gateway_obs so a from-scratch rebuild reproduces the mqmon browse-only observer (hot-deployed during epic #175)."
```

---

## Task 3: `gw-sec-queues` — declare-and-verify Stage 2 + `setmqaut -g` grants

**Files:** Modify `ansible/roles/gw-sec-queues/defaults/main.yml` + `tasks/main.yml`.

**Interfaces:** Consumes the base lab's Stage-2 groups (`mqmon`, `mqapp`). Produces
the enforced grants on our audit + instruction + reply queues.

**Context:** Append after the role's existing `DEFINE`/`ALTER`/declare-verify
tasks, in the `become_user: mqm` idiom. Use `setmqaut -g` (the base lab's
mechanism), not `SET AUTHREC`. Stage 2 (accounts + CHLAUTH) is a §6.0 prerequisite
established by the deployment task; this role **declare-and-verifies** it before
granting.

- [ ] **Step 1: Read** the role's current tail and `defaults/main.yml` (`q_*` names);
  read the base lab's `group_vars/all/authz.yml` grant shape to mirror it.

- [ ] **Step 2: Add the grant map to defaults** (`ansible/roles/gw-sec-queues/defaults/main.yml`):

```yaml
# --- least-privilege authority model (epic #235) — mirrors the base lab -------
# Reuses the base lab's Stage-2 groups (mqapp/mqmon); grants via setmqaut -g on
# the queues this repo owns. Audit ledgers are QM-written (STREAMQ) so they take
# NO client +put/+setall — only mqmon +browse. Shape mirrors base lab authz.yml.
gw_sec_observer_group: mqmon    # MON.SVRCONN observers (collector + exporter)
gw_sec_app_group: mqapp         # APP.SVRCONN application

gw_sec_grants:
  - { group: "{{ gw_sec_observer_group }}", object: "{{ q_instruct_audit }}", authorities: "+browse +inq +dsp" }
  - { group: "{{ gw_sec_observer_group }}", object: "{{ q_status_audit }}",   authorities: "+browse +inq +dsp" }
  - { group: "{{ gw_sec_app_group }}",      object: "{{ q_instruct }}",       authorities: "+put" }
  - { group: "{{ gw_sec_app_group }}",      object: "{{ q_instruct_audit }}", authorities: "+put" }
  - { group: "{{ gw_sec_app_group }}",      object: "{{ q_reply }}",          authorities: "+get +inq +browse" }
```

- [ ] **Step 3: Declare-and-verify Stage 2 is present** (append to `tasks/main.yml`):

```yaml
# --- least-privilege authority model (epic #235) ------------------------------
- name: Declare-and-verify the base lab's Stage-2 observer/app groups exist
  ansible.builtin.command: "id {{ item }}"
  become: true
  loop:
    - "{{ gw_sec_observer_group }}"
    - "{{ gw_sec_app_group }}"
  register: gw_sec_group_check
  changed_when: false
  failed_when: gw_sec_group_check.rc != 0
  # A missing account means Stage 2 was never applied to this box (the deployment
  # task's §6.0 prerequisite). Fail loudly rather than grant to a non-existent group.

- name: Declare-and-verify CHLAUTH is enabled (channels no longer run as mqm)
  ansible.builtin.shell: |
    set -o pipefail
    echo "DISPLAY QMGR CHLAUTH" | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  register: gw_sec_chlauth
  changed_when: false
  failed_when: "gw_sec_chlauth.rc != 0 or 'CHLAUTH(ENABLED)' not in gw_sec_chlauth.stdout"
```

- [ ] **Step 4: Apply the `setmqaut -g` grants** (mirrors the base lab's `su mqm -c "setmqaut …"` loop):

```yaml
- name: Apply the least-privilege authority grants (setmqaut -g on our GW.SEC.* objects)
  ansible.builtin.shell: |
    set -o pipefail
    /opt/mqm/bin/setmqaut -m {{ qmgr_name }} -n {{ item.object }} -t queue -g {{ item.group }} {{ item.authorities }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  loop: "{{ gw_sec_grants }}"
  register: gw_sec_setmqaut
  changed_when: "'completed successfully' in gw_sec_setmqaut.stdout"
  failed_when: gw_sec_setmqaut.rc != 0
```

(`setmqaut` takes each authority as its own `+xxx` token — `{{ item.authorities }}`
expands to the space-separated list. Re-running with the same grants is idempotent.)

- [ ] **Step 5: Verify the grants** (mqmon browses but cannot get/put; mqapp holds no browse):

```yaml
- name: Verify mqmon holds browse but NOT get/put on an audit ledger
  ansible.builtin.command: "/opt/mqm/bin/dspmqaut -m {{ qmgr_name }} -n {{ q_instruct_audit }} -t queue -g {{ gw_sec_observer_group }}"
  become: true
  become_user: mqm
  register: gw_sec_mon_aut
  changed_when: false
  failed_when: >-
    gw_sec_mon_aut.rc != 0 or
    'browse' not in gw_sec_mon_aut.stdout or
    'get' in gw_sec_mon_aut.stdout or
    'put' in gw_sec_mon_aut.stdout

- name: Verify mqapp holds NO browse/get on the outbound audit ledger
  ansible.builtin.command: "/opt/mqm/bin/dspmqaut -m {{ qmgr_name }} -n {{ q_instruct_audit }} -t queue -g {{ gw_sec_app_group }}"
  become: true
  become_user: mqm
  register: gw_sec_app_aut
  changed_when: false
  # mqapp may hold +put here; it must NOT hold browse or get.
  failed_when: >-
    gw_sec_app_aut.rc != 0 or
    'browse' in gw_sec_app_aut.stdout or
    'get' in gw_sec_app_aut.stdout
```

- [ ] **Step 6: Validate** — `vrg-container-run -- vrg-validate` → PASS.

- [ ] **Step 7: Commit**

```bash
vrg-commit --type feat --scope gw-sec-queues \
  --message "grant least-privilege authorities on the GW.SEC.* objects (setmqaut -g)" \
  --body "Epic #235: reuse the base lab's mqmon/mqapp groups; setmqaut -g grants mqmon +browse+inq+dsp on the audit ledgers (QM-written via STREAMQ, so no +put/+setall) and mqapp +put on the instruction path + get/inq/browse on the reply. Declare-and-verify Stage 2 (groups exist, CHLAUTH enabled); dspmqaut verifies mqmon has no get/put and mqapp no browse."
```

---

## Task 4: `gw-sec-svc-queues` — `mqsvc` reply grant + responder side

**Files:** Modify `ansible/roles/gw-sec-svc-queues/defaults/main.yml` + `tasks/main.yml`.

**Interfaces:** Grants the return-MCA identity `mqsvc` `+put +setall` on
`GW.SEC.REPLY` (it writes replies there), and the responder's least-privilege
grants on the `SVCQM` objects this role owns.

**Context:** `GW.SEC.REPLY` lives on our QM (`gw-sec-queues` defines it) but is
written by the inter-QM **return MCA** (`mqsvc`) — under `PUTAUT(DEF)` the MCA
opens the destination with `MQOO_SET_ALL_CONTEXT`, so it needs `+put +setall`
(base lab context-authority finding, spec §10). The responder-side objects on
`SVCQM` (landing queue it consumes, reply target it puts to) get the responder's
Stage-2 identity's grants. Read the role first for the exact object + qmgr vars.

- [ ] **Step 1: Read** `gw-sec-svc-queues/tasks/main.yml` + `defaults/main.yml`
  (object names, `svc_qmgr`/`our_qmgr`), and confirm which role/QM owns
  `GW.SEC.REPLY` — the `mqsvc` grant lands wherever `GW.SEC.REPLY` is defined
  (our QM). If `GW.SEC.REPLY` is owned by `gw-sec-queues`, add the `mqsvc` grant
  there (Task 3's grant map) instead and scope this task to the `SVCQM` responder
  objects; the intent is one grant per owning role.

- [ ] **Step 2: Add the grant map to defaults**

```yaml
# --- least-privilege authority model (epic #235), reply + responder side ------
gw_sec_mca_group: mqsvc            # the inter-QM return MCA writes GW.SEC.REPLY
gw_sec_responder_group: ""         # REQUIRED — SVC.SVRCONN Stage-2 identity on SVCQM (declare-and-verify)

# mqsvc on GW.SEC.REPLY (our QM): +put +setall (context authority for the MCA).
gw_sec_reply_grant:
  - { group: "{{ gw_sec_mca_group }}", object: "{{ q_reply }}", authorities: "+put +setall" }

# Responder grants on the objects this role owns on SVCQM.
gw_sec_svc_grants:
  - { group: "{{ gw_sec_responder_group }}", object: "<landing-queue-var>",  authorities: "+get +inq" }
  - { group: "{{ gw_sec_responder_group }}", object: "<reply-target-var>",   authorities: "+put" }
```

Replace `<landing-queue-var>`/`<reply-target-var>` with the real names from Step 1.

- [ ] **Step 3: Add declare-and-verify + `setmqaut -g` loops + `dspmqaut` verify**
  mirroring Task 3 Steps 3–5, targeting the correct queue manager for each grant
  (`GW.SEC.REPLY` on our QM; the responder objects on `svc_qmgr`). Verify `mqsvc`
  holds `+put +setall` on `GW.SEC.REPLY` and nothing on the audit ledgers.

- [ ] **Step 4: Validate** — `vrg-container-run -- vrg-validate` → PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope gw-sec-svc-queues \
  --message "grant mqsvc +put+setall on the reply and least-privilege responder grants" \
  --body "Epic #235: the inter-QM return MCA (mqsvc) needs +put +setall on GW.SEC.REPLY (context authority under PUTAUT(DEF)); the responder gets get/put on its own SVCQM objects. setmqaut -g, declare-and-verify, dspmqaut-verified."
```

---

## Task 5: Docs — "Security & permissions" section

**Files:** Modify `docs/mq-native-settlement-tracking.md` + `ansible/README.md`.

- [ ] **Step 1: Read** the doc's headings for the insertion point.

- [ ] **Step 2: Write the section** covering: the grant model (the spec §5 table);
  the **reuse of the base lab's `mqapp`/`mqmon`/`mqsvc` identities** and its
  `SSLPEERMAP`/`setmqaut -g` mechanism; that the audit ledgers are QM-written so
  observers need only `+browse`; the **ownership boundary** (base lab owns the
  channel identities + Stage 2, generic; we own the `GW.SEC.*` grants); and that
  browse-only is now QM-enforced, proven by the 2035 test. Cross-reference the base
  lab's `mq-authorization-guide.md`.

- [ ] **Step 3: Add the `ansible/README.md` pointer** noting `gw-sec-queues`/
  `gw-sec-svc-queues` add the `setmqaut -g` grants for the base lab's groups and
  the exporter connects as `mqmon`.

- [ ] **Step 4: Validate** — `vrg-container-run -- vrg-validate` → PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type docs --scope security \
  --message "document the least-privilege model, reusing the base lab's authz identities" \
  --body "Epic #235: Security & permissions section — the grant model, reuse of mqapp/mqmon/mqsvc + SSLPEERMAP + setmqaut -g, the QM-written audit ledgers, and the ownership boundary."
```

---

## Operational Task A (deployment): establish Stage 2, then apply the model

Filed with `vrg-issue-create --kind deployment`, **blocked-by Tasks 1–4**. Not
PR-workable; run with `issue-deploy`. Closes on `Outcome: SUCCESS`.

- **§6.0 — Establish the base lab's Stage 2 on `NHAUAPP` (prerequisite).** The box
  is pre-hardening (no `mqapp`/`mqmon`/`mqsvc`, `CHLAUTH(DISABLED)`). Apply the base
  lab's **own generic** authorization (`site-nativeha-ubuntu.yml` authz —
  `mq-authz-accounts` + `authz.mqsc.j2` CHLAUTH maps + its base `setmqaut -g`).
  Owned/automated from this repo (invoke the base lab playbook), declare-and-verified.
  Nothing bolt-on-specific is added to the base lab.
- **Precondition self-check:** Tasks 1–4 merged; the `mq_prometheus` (OU=ops)
  keystore + Stage-2 apply inputs available. If unmet, comment "blocked:
  preconditions not met" and stop.
- **Procedure:** (1) apply base lab Stage 2 to `NHAUAPP`; verify `mqapp`/`mqmon`/
  `mqsvc` exist and `CHLAUTH(ENABLED)`. (2) apply our settlement plays from
  `site.yml` (`gw-sec-queues`, `gw-sec-svc-queues`, `gw-sec-observability`,
  `gw-sec-collector`) with the identity/keystore inputs. The roles' declare-and-verify
  + `dspmqaut` assertions gate the apply.
- **Acceptance:** both applies complete green; `dspmqaut` shows the intended grants;
  the channels resolve to `mqapp`/`mqmon` (not `mqm`); exporter + collector run as `mqmon`.

## Operational Task B (validation): prove the model — positive paths + 2035 denials

Filed with `vrg-issue-create --kind validation`, **blocked-by Operational Task A**.
Not PR-workable; run with `issue-validate`. Closes on `Outcome: SUCCESS`.

- **Precondition self-check (blocks if unmet):** Stage 2 present — `mqapp`/`mqmon`/
  `mqsvc` exist, `CHLAUTH(ENABLED)`, and `DISPLAY CHSTATUS … MCAUSER` shows the
  channels resolving to `mqapp`/`mqmon`, **not** `mqm`. If any channel still resolves
  to `mqm`, comment the blocked reason and **stop** (never fabricate a pass).
- **Positive paths:** `mqapp` puts an instruction → streams to counterparty +
  retained; a reply lands on `GW.SEC.REPLY` (written by `mqsvc`) → streams to
  `GW.SEC.STATUS.AUDIT`; both observers (`mqmon`: collector + reidentified exporter)
  browse both ledgers and metrics advance. No regression.
- **Negative denials (the load-bearing proof):** under `mqmon`, a destructive
  `MQGET` and an `MQPUT` on an audit ledger each return **MQRC 2035**; under `mqapp`,
  an `MQOO_BROWSE` of an audit ledger returns **2035**.
- **No-wider-grant enumeration:** `dmpmqaut`/`DISPLAY AUTHREC` on each audit ledger
  confirms only `mqmon` (+ the `mqm` admin default) holds browse, and `mqapp` holds
  no browse/get.
- **Boundary assertion:** Stage 2 came from the base lab's generic playbook; our added
  change is only the `GW.SEC.*` `setmqaut -g` grants + the exporter identity config.
- **Acceptance:** all positive paths green AND all three denials return 2035; record
  the evidence in the `Outcome: SUCCESS` comment. Model the harness on the base lab's
  `site-nativeha-ubuntu-authz-validate.yml`.

---

## Self-Review

**Spec coverage:**
- Spec §6.0 (establish Stage 2) → Operational Task A prerequisite.
- Spec §2/§6.1–.2 (mqmon observer; collector wiring) → Tasks 1, 2.
- Spec §5 grant table → Task 3 (mqmon/mqapp) + Task 4 (mqsvc/responder).
- Spec §5 invariants (audit ledgers QM-written, no +setall; mqapp no browse;
  groups not principals) → Task 3 defaults + Step 5 verify; Task 4 `mqsvc` confined
  to `GW.SEC.REPLY`.
- Spec §3 declare-and-verify Stage 2 → Task 3 Step 3; Operational Task A/B preconditions.
- Spec §6.5 docs → Task 5.
- Spec §7 validation (Stage-2 precondition + positive + 2035 + boundary) → Operational Tasks A/B.

**Resolved spec open questions:** principal-vs-group → **groups** (base lab);
identity discovery → declare-and-verify the base lab's `SSLPEERMAP` Stage 2.

**Placeholder scan:** Task 4 Step 2 carries `<landing-queue-var>`/`<reply-target-var>`
because the exact `SVCQM` object names must be read from the role in Step 1 (the step
says so). Everything else is concrete.

**Type/name consistency:** `gw_sec_observer_group` (mqmon), `gw_sec_app_group`
(mqapp), `gw_sec_mca_group` (mqsvc), `gw_sec_responder_group`, `gw_sec_grants`,
`gw_sec_reply_grant`, `gw_sec_svc_grants`, `gw_sec_exporter_mon_p12/_pw` are
used identically across defaults, tasks, and the operational tasks.
