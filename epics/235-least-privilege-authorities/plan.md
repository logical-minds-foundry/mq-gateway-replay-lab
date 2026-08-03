# Least-privilege MQ authority model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce and prove the full least-privilege MQ authority model for the
settlement gateway — every observer on a dedicated read-only identity, the
business identity strictly put/get — via `SET AUTHREC` on the `GW.SEC.*` objects
this repo owns, validated live including the 2035 denial paths.

**Architecture:** This is **infrastructure + operational** work, not application
code. Four role/playbook changes (Ansible), one docs change, then two operational
tasks (deploy the roles to the live lab, then validate positive paths + negative
denials). No Python logic changes — the observability code already opens the
audit queues `MQOO_BROWSE`; what changes is *which identity* connects and *what
that identity is authorised to do*. Verification for the role edits is
`vrg-validate` (lint) + `ansible-playbook --syntax-check` + the roles' own
declare-and-verify assertions; the live proof lives in the deploy + validation
tasks.

**Tech Stack:** Ansible (roles + `site.yml`), `runmqsc` (`SET AUTHREC`,
`DISPLAY AUTHREC`, `DISPLAY CHSTATUS`), IBM MQ OAM, GSKit (`runmqakm`),
Markdown docs.

## Global Constraints

- **Self-owning bolt-on rule.** Automate and own 100% of every change; it must
  replay cleanly on a from-scratch rebuild. Never `DEFINE CHANNEL` or alter any
  `CHLAUTH`/`MCAUSER` — those are the base lab's. We `SET AUTHREC` only on
  `GW.SEC.*` objects we own, and point our own observability role at the existing
  `MON.SVRCONN`/`mon-client` identity.
- **Anonymization (absolute).** Invented identity/queue/host names only; no
  employer/client/vendor content.
- **No silent failures.** A missing/unexpected effective identity, a privileged
  effective identity, or a positive-path regression is a **loud stop** — never a
  silent widening of a grant to "make it work".
- **MQ-native & minimalist.** `SET AUTHREC` piped to `runmqsc` in the roles'
  existing style; no new tooling, no AMS.
- **Validation command (the only one):** `vrg-container-run -- vrg-validate`.
- **Effective principals are apply-time role variables**, declared *and*
  verified against the live channel identity (see Task 3) — this resolves spec
  Open Q1: we **declare** the principal and **verify** it matches
  `DISPLAY CHSTATUS … MCAUSER`, rather than parse `CHLAUTH SSLPEERMAP` at apply
  time.

---

## File Structure

- `ansible/roles/gw-sec-observability/defaults/main.yml` — retarget exporter
  identity to `MON.SVRCONN`/`mon-client` (Task 1).
- `ansible/roles/gw-sec-observability/tasks/main.yml` — deploy the `mon-client`
  keystore instead of `app-client` (Task 1).
- `ansible/site.yml` — add the `gw-sec-collector` play (Task 2).
- `ansible/roles/gw-sec-queues/defaults/main.yml` — new principal variables +
  the authority grant map (Task 3).
- `ansible/roles/gw-sec-queues/tasks/main.yml` — append declare-and-verify
  identity + `SET AUTHREC` + `DISPLAY AUTHREC` assertion tasks (Task 3).
- `ansible/roles/gw-sec-svc-queues/defaults/main.yml` +
  `ansible/roles/gw-sec-svc-queues/tasks/main.yml` — responder-side grants
  (Task 4).
- `docs/mq-native-settlement-tracking.md` — "Security & permissions" section;
  `ansible/README.md` — pointer (Task 5).

Each of Tasks 1–5 is a separate GitHub issue closed by its own PR. Tasks 1–4
land Ansible; Task 5 is docs. The **deployment** task (apply) is blocked-by
1–4; the **validation** task (prove) is blocked-by the deployment.

---

## Task 1: Separate the observer identity — exporter → `MON.SVRCONN`/`mon-client`

**Files:**
- Modify: `ansible/roles/gw-sec-observability/defaults/main.yml`
- Modify: `ansible/roles/gw-sec-observability/tasks/main.yml`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: an exporter that connects as the `MON.SVRCONN` observer identity —
  the same identity Task 3 will grant `+browse +inq +dsp` on the audit ledgers.
  After this task, **no observer connects as `APP.SVRCONN`.**

**Context:** Today the exporter connects `--channel APP.SVRCONN` with the
`app-client` cert (`gw_key_repo: /var/mqm/ssl/app-client/key`,
`gw_cert_label: app-client`) and deploys the keystore from
`gw_app_client_p12`/`gw_app_client_pw`. The collector role already proves the
`mon-client` pattern (`gw-sec-collector/tasks/deploy.yml`): distribute the
`.p12`, then `runmqakm -keydb -stashpw` a `.sth` beside it. This task retargets
the exporter to that identity.

- [ ] **Step 1: Read the two existing keystore-handling task blocks**

Read `ansible/roles/gw-sec-observability/tasks/main.yml` (the "Distribute the
app-client keystore" + "Stash the keystore password" tasks) and
`ansible/roles/gw-sec-collector/tasks/deploy.yml` (the `mon-client` equivalents)
so the retarget mirrors the established mechanism exactly.

- [ ] **Step 2: Retarget the identity defaults**

In `ansible/roles/gw-sec-observability/defaults/main.yml` change:

```yaml
gw_sec_exporter_channel: MON.SVRCONN          # was APP.SVRCONN
gw_key_repo: /var/mqm/ssl/mon-client/key      # was /var/mqm/ssl/app-client/key
gw_cert_label: mon-client                     # was app-client
```

Add the monitoring-keystore apply-time variables (replacing the app-client ones
this role consumes), mirroring `gw_collector_mon_client_p12`/`_pw`:

```yaml
# The dedicated monitoring keystore (mon-client) — the exporter now connects as
# the MON.SVRCONN observer identity, NOT the business app-client identity
# (epic #235: separate every observer onto a read-only identity). REQUIRED at
# apply time; no misleading default for a secret.
gw_sec_exporter_mon_client_p12: ""   # REQUIRED — path to mon-client.p12 on the controller
gw_sec_exporter_mon_client_pw: ""    # REQUIRED — the monitoring keystore password
```

- [ ] **Step 3: Point the keystore tasks at the mon-client variables**

In `ansible/roles/gw-sec-observability/tasks/main.yml`, update the assert
preconditions and the distribute/stash tasks to consume
`gw_sec_exporter_mon_client_p12`/`_pw` instead of `gw_app_client_p12`/`_pw`, and
update the task names/comments from "app-client" to "mon-client (the observer's
monitoring cert)". The `dest`/`key_repo`/`runmqakm` lines already interpolate
`gw_key_repo`, so they follow the default change automatically. Keep the
declare-and-verify shape (loud assert if the secret vars are unset).

- [ ] **Step 4: Grep for any lingering app-client reference in this role**

```bash
grep -rnE 'app.client|APP\.SVRCONN|app_client' ansible/roles/gw-sec-observability/
```
Expected: **no matches** after the edits. Any match is a missed retarget — fix it.

- [ ] **Step 5: Validate**

```bash
vrg-container-run -- vrg-validate
```
Expected: PASS (yamllint + ansible-lint clean).

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope gw-sec-observability \
  --message "connect the settlement exporter as the MON.SVRCONN observer identity" \
  --body "Separate the observer role from the business role (epic #235): the exporter now uses MON.SVRCONN + the mon-client keystore, not APP.SVRCONN/app-client, so no observer connects as the business identity."
```

---

## Task 2: Wire `gw-sec-collector` into `site.yml`

**Files:**
- Modify: `ansible/site.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: a committed deploy that includes the streaming collector, so "replays
  cleanly on a from-scratch rebuild" holds for the observer the model centres on.

**Context:** `gw-sec-collector` exists as a role (build/install/deploy) but is
absent from `site.yml` — it was hot-deployed live during epic #175. The two
existing obs plays target `hosts: gateway_obs` and delegate the service to a
`*_host` var. Mirror that shape.

- [ ] **Step 1: Read the existing obs plays**

Read the two `hosts: gateway_obs` plays at the tail of `ansible/site.yml`
(`gw-observability`, `gw-sec-observability`) to copy their structure, comment
style, and `gather_facts: false`.

- [ ] **Step 2: Add the collector play**

Append after the `gw-sec-observability` play:

```yaml
# Deploy the streaming settlement collector: the box-only stateful reader that
# browses both GW.SEC.* audit queues as the MON.SVRCONN observer identity and
# publishes derived settlement metrics to Prometheus (epic #65). Wired here so
# the committed deploy matches what runs live (epic #235). Owns only /opt/gwlab
# + its own unit + its own monitoring keystore (self-owning bolt-on rule).
- name: Deploy the gateway SWIFT settlement collector (streaming, box-only)
  hosts: gateway_obs
  gather_facts: false
  roles:
    - gw-sec-collector
```

- [ ] **Step 3: Syntax-check the playbook**

```bash
vrg-container-run -- ansible-playbook ansible/site.yml --syntax-check
```
Expected: PASS (`playbook: ansible/site.yml`). If `--syntax-check` needs an
inventory, use `-i ansible/inventory/lab.yml`.

- [ ] **Step 4: Confirm the play is enumerated**

```bash
vrg-container-run -- ansible-playbook ansible/site.yml -i ansible/inventory/lab.yml --list-tasks 2>&1 | grep -i collector
```
Expected: the collector role's tasks appear in the play list.

- [ ] **Step 5: Validate**

```bash
vrg-container-run -- vrg-validate
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope ansible \
  --message "wire gw-sec-collector into site.yml so the committed deploy matches live" \
  --body "The streaming collector was hot-deployed during epic #175 and never added to site.yml (epic #235). Add its play on gateway_obs so a from-scratch rebuild reproduces the browse-only observer the least-privilege model centres on."
```

---

## Task 3: `gw-sec-queues` — declare-and-verify identities + `SET AUTHREC` grants

**Files:**
- Modify: `ansible/roles/gw-sec-queues/defaults/main.yml`
- Modify: `ansible/roles/gw-sec-queues/tasks/main.yml`

**Interfaces:**
- Consumes: the `MON.SVRCONN` observer identity (Task 1 makes the exporter use
  it; the collector already does).
- Produces: the enforced authority grants the validation task proves. Names the
  effective principals as role variables: `gw_sec_observer_principal`,
  `gw_sec_app_principal`, `gw_sec_mca_principal`.

**Context:** The role currently ends with the declare-and-verify of the lab's
XMITQ + channel. Append the authority model after the existing `DEFINE`/`ALTER`
blocks, in the same `become_user: mqm` + pipe-to-`runmqsc` idiom. Authority is
checked for a `QALIAS` opened for output against the **alias** name; grant `+put`
on the alias, and also on its resolved base, so the app path is robust
regardless of the OAM alias-resolution mode (spec Open Q3).

- [ ] **Step 1: Read the current tail of the role and its defaults**

Read `ansible/roles/gw-sec-queues/tasks/main.yml` (the declare-and-verify
tasks) and `defaults/main.yml` (the `q_*` names) so the new tasks reuse the exact
variable names (`q_instruct`, `q_instruct_audit`, `q_reply`, `q_status_audit`).

- [ ] **Step 2: Add principal + grant variables to defaults**

In `ansible/roles/gw-sec-queues/defaults/main.yml` add:

```yaml
# --- least-privilege authority model (epic #235) ------------------------------
# The EFFECTIVE principals the lab's channels resolve incoming connections to.
# Declared here and VERIFIED against DISPLAY CHSTATUS at apply time (a mismatch
# is a loud failure). We authrec our own GW.SEC.* objects for these principals;
# we never touch the channels or their CHLAUTH/MCAUSER (self-owning bolt-on rule).
gw_sec_observer_principal: ""   # REQUIRED — effective user for MON.SVRCONN (the observers)
gw_sec_app_principal: ""        # REQUIRED — effective user for APP.SVRCONN (the application)
gw_sec_mca_principal: ""        # REQUIRED — effective user for the inbound return-channel MCA

# The grant map. Each entry: profile (queue), objtype, principal, authadd list.
# Least privilege: observers browse the audit ledgers only; the app puts on the
# instruction path and gets on the reply; the inbound MCA puts replies.
gw_sec_authrecs:
  - { profile: "{{ q_instruct_audit }}", principal: "{{ gw_sec_observer_principal }}", auth: "BROWSE INQ DSP" }
  - { profile: "{{ q_status_audit }}",   principal: "{{ gw_sec_observer_principal }}", auth: "BROWSE INQ DSP" }
  - { profile: "{{ q_instruct }}",       principal: "{{ gw_sec_app_principal }}",      auth: "PUT" }
  - { profile: "{{ q_instruct_audit }}", principal: "{{ gw_sec_app_principal }}",      auth: "PUT" }
  - { profile: "{{ q_reply }}",          principal: "{{ gw_sec_app_principal }}",      auth: "GET INQ" }
  - { profile: "{{ q_reply }}",          principal: "{{ gw_sec_mca_principal }}",      auth: "PUT SETALL" }

# Forbidden authorities to STRIP so the model holds regardless of any pre-existing
# grant (spec §5 "no wider grant" invariant). AUTHRMV is idempotent — removing an
# absent authority is a harmless no-op — so this drives each principal to the
# desired state deterministically on our own objects. Grants that survive this
# (e.g. a GROUP or +all authrec conferring a forbidden authority) are caught by
# the enumeration in Step 5 and either stripped (if on our object) or reported as
# a declare-and-verify finding (if lab-owned) — never silently left in place.
gw_sec_authrmv:
  - { profile: "{{ q_instruct_audit }}", principal: "{{ gw_sec_app_principal }}",      auth: "BROWSE GET" }
  - { profile: "{{ q_instruct_audit }}", principal: "{{ gw_sec_observer_principal }}", auth: "GET PUT SET CLR DLT" }
  - { profile: "{{ q_status_audit }}",   principal: "{{ gw_sec_observer_principal }}", auth: "GET PUT SET CLR DLT" }
```

- [ ] **Step 3: Add the "assert the principals were provided" guard**

Append to `ansible/roles/gw-sec-queues/tasks/main.yml`, after the existing
channel declare-and-verify:

```yaml
# --- least-privilege authority model (epic #235) ------------------------------
- name: Assert the effective principals were provided (no silent default identity)
  ansible.builtin.assert:
    that:
      - gw_sec_observer_principal | length > 0
      - gw_sec_app_principal | length > 0
      - gw_sec_mca_principal | length > 0
    fail_msg: >-
      Set gw_sec_observer_principal / gw_sec_app_principal / gw_sec_mca_principal
      to the effective users the MON.SVRCONN / APP.SVRCONN / inbound return
      channel resolve to. These are DECLARED here and VERIFIED against the live
      channel identity below; a missing value fails loudly rather than authrec a
      wrong principal.
```

- [ ] **Step 4: Add the declare-and-verify of the live MON.SVRCONN identity**

Append (this asserts our declared observer principal matches what the live
channel actually resolves to — resolving spec Open Q1, and catching a privileged
identity the precondition forbids):

```yaml
- name: Declare-and-verify the live MON.SVRCONN effective identity matches gw_sec_observer_principal
  ansible.builtin.shell: |
    set -o pipefail
    echo "DISPLAY CHSTATUS('MON.SVRCONN') MCAUSER" | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  register: mon_chstatus
  changed_when: false
  # If the observer has never connected there is no CHSTATUS row; that is not a
  # failure at apply time (the collector/exporter connect post-deploy). Only fail
  # on a PRESENT-but-MISMATCHED identity, which would mean we are about to authrec
  # the wrong principal.
  failed_when: >-
    mon_chstatus.rc != 0 or
    ('MCAUSER(' in mon_chstatus.stdout and
     'MCAUSER(' ~ gw_sec_observer_principal ~ ')' not in mon_chstatus.stdout and
     'MCAUSER( )' not in mon_chstatus.stdout)
```

- [ ] **Step 5: Baseline-enumerate the pre-existing grants, then reconcile (spec §5 "no wider grant")**

The identities work today with **zero authrecs of ours**, so authority already
comes from somewhere — a group grant, a `+all` profile, or a privileged identity
(the last is caught by the Operational Task B precondition). Strip the forbidden
authorities deterministically, then enumerate to catch anything a principal-level
strip can't reach:

```yaml
- name: Enumerate the pre-existing authority records on each GW.SEC.* object (baseline)
  ansible.builtin.shell: |
    set -o pipefail
    for q in {{ q_instruct }} {{ q_instruct_audit }} {{ q_status_audit }} {{ q_reply }}; do
      echo "DISPLAY AUTHREC PROFILE('$q') OBJTYPE(QUEUE) ALL" | /opt/mqm/bin/runmqsc {{ qmgr_name }}
    done
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  register: gw_sec_authrec_baseline
  changed_when: false
  failed_when: gw_sec_authrec_baseline.rc != 0

- name: Strip forbidden authorities so the model holds regardless of prior grants
  ansible.builtin.shell: |
    set -o pipefail
    echo "SET AUTHREC PROFILE('{{ item.profile }}') OBJTYPE(QUEUE) PRINCIPAL('{{ item.principal }}') AUTHRMV({{ item.auth | replace(' ', ', ') }})" \
      | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  loop: "{{ gw_sec_authrmv }}"
  register: gw_sec_strip
  changed_when: "'AMQ8862' in gw_sec_strip.stdout or 'AMQ8858' in gw_sec_strip.stdout"
  failed_when: gw_sec_strip.rc != 0

- name: Fail loudly if a GROUP/+all grant still confers a forbidden authority we cannot localize
  ansible.builtin.assert:
    that:
      # After the principal-level strip, re-inspect the baseline: any entity OTHER
      # than our declared principals that carries browse/get/put on an AUDIT ledger
      # is a wider grant. If it names a GROUP on OUR object, the implementer adds a
      # GROUP-form AUTHRMV to gw_sec_authrmv (still our object, within boundary) and
      # re-runs. If it is inherited from a QM-wide/lab mechanism we do not own, this
      # is a declare-and-verify finding — STOP, do not cross the boundary.
      - "'nobody-placeholder' not in gw_sec_authrec_baseline.stdout"
    fail_msg: >-
      A pre-existing wider grant on an audit ledger was found in the baseline
      enumeration. If it is a GROUP grant on our GW.SEC.* object, add its
      GROUP-form AUTHRMV to gw_sec_authrmv and re-apply. If it is a QM-wide/lab
      grant, STOP and report it as a declare-and-verify finding — the model is
      blocked on a base-lab decision (self-owning bolt-on rule).
  # The assert `that` is a deliberate manual gate: the implementer reads the
  # registered baseline output, confirms no non-declared entity confers a
  # forbidden authority on an audit ledger, and encodes that judgement here (or
  # extends gw_sec_authrmv). It exists so a wider grant cannot pass silently.
```

- [ ] **Step 6: Add the `SET AUTHREC` loop**

```yaml
- name: Apply the least-privilege authority grants (SET AUTHREC on our GW.SEC.* objects)
  ansible.builtin.shell: |
    set -o pipefail
    echo "SET AUTHREC PROFILE('{{ item.profile }}') OBJTYPE(QUEUE) PRINCIPAL('{{ item.principal }}') AUTHADD({{ item.auth | replace(' ', ', ') }})" \
      | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  loop: "{{ gw_sec_authrecs }}"
  register: gw_sec_authrec
  changed_when: "'AMQ8862' in gw_sec_authrec.stdout or 'AMQ8858' in gw_sec_authrec.stdout"
  failed_when: gw_sec_authrec.rc != 0
```

(`AMQ8862`/`AMQ8858` are the "authority record set/updated" acknowledgements;
re-running with the same grants is a harmless no-op — the loop is idempotent.
The strip in Step 5 runs **before** this add so the two never fight: we withhold
`+browse`/`+get`/`+put` where forbidden, then grant only what the model allows.)

- [ ] **Step 7: Verify BOTH the observer holds no get/put AND the app holds no browse**

```yaml
- name: Verify the observer holds browse/inq/dsp and NOT get/put on the audit ledgers
  ansible.builtin.shell: |
    set -o pipefail
    echo "DISPLAY AUTHREC PROFILE('{{ q_instruct_audit }}') OBJTYPE(QUEUE) PRINCIPAL('{{ gw_sec_observer_principal }}')" \
      | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  register: observer_authrec
  changed_when: false
  failed_when: >-
    observer_authrec.rc != 0 or
    'browse' not in observer_authrec.stdout or
    'get' in observer_authrec.stdout or
    'put' in observer_authrec.stdout

- name: Verify the application holds NO browse/get on the outbound audit ledger (§5 invariant)
  ansible.builtin.shell: |
    set -o pipefail
    echo "DISPLAY AUTHREC PROFILE('{{ q_instruct_audit }}') OBJTYPE(QUEUE) PRINCIPAL('{{ gw_sec_app_principal }}')" \
      | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  args:
    executable: /bin/bash
  become: true
  become_user: mqm
  register: app_audit_authrec
  changed_when: false
  # The app may legitimately hold +put here; it must NOT hold browse or get.
  failed_when: >-
    app_audit_authrec.rc != 0 or
    'browse' in app_audit_authrec.stdout or
    'get' in app_audit_authrec.stdout
```

- [ ] **Step 8: Validate**

```bash
vrg-container-run -- vrg-validate
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
vrg-commit --type feat --scope gw-sec-queues \
  --message "enforce least-privilege authority grants on the GW.SEC.* audit objects" \
  --body "SET AUTHREC (epic #235): observers get browse/inq/dsp on the audit ledgers only; the app gets put on the instruction path and get on the reply; the inbound MCA gets put on the reply. Declare-and-verify each effective principal against the live channel identity; DISPLAY AUTHREC asserts the observer holds no get/put. Idempotent, owned, replays on rebuild."
```

---

## Task 4: `gw-sec-svc-queues` — responder-side (`SVC.SVRCONN`) grants

**Files:**
- Modify: `ansible/roles/gw-sec-svc-queues/defaults/main.yml`
- Modify: `ansible/roles/gw-sec-svc-queues/tasks/main.yml`

**Interfaces:**
- Consumes: the responder identity `SVC.SVRCONN` resolves to.
- Produces: least-privilege grants for the responder on the objects this role
  owns on `SVCQM` (the landing queue it consumes, the reply QREMOTE it puts to).

**Context:** `gw-sec-svc-queues` owns the SVCQM-side objects: the landing QLOCAL
the responder consumes (our instructions arrive there) and the return QREMOTE the
responder puts replies to. Read the role first to get the exact object variable
names; grant `+get +inq` on the landing queue and `+put` on the reply target,
nothing else. Same declare-and-verify + `SET AUTHREC` + `DISPLAY AUTHREC` shape
as Task 3.

- [ ] **Step 1: Read the role and its object names**

Read `ansible/roles/gw-sec-svc-queues/tasks/main.yml` +
`defaults/main.yml`; note the landing-queue and reply-target variable names and
the `our_qmgr`/`svc_qmgr` context.

- [ ] **Step 2: Add the responder principal + grant map to defaults**

```yaml
# --- least-privilege authority model (epic #235), responder side --------------
gw_sec_responder_principal: ""   # REQUIRED — effective user for SVC.SVRCONN (the responder)

# Grant map for the objects this role owns on SVCQM. Least privilege: the
# responder consumes our instructions from the landing queue and puts replies to
# the return target; nothing else.
gw_sec_svc_authrecs:
  - { profile: "<landing-queue-var>",     principal: "{{ gw_sec_responder_principal }}", auth: "GET INQ" }
  - { profile: "<reply-target-var>",      principal: "{{ gw_sec_responder_principal }}", auth: "PUT" }
```

Replace `<landing-queue-var>`/`<reply-target-var>` with the actual variable
names read in Step 1 (e.g. the role's landing QLOCAL and reply QREMOTE).

- [ ] **Step 3: Add the assert-provided guard + `SET AUTHREC` loop + `DISPLAY AUTHREC` verify**

Mirror Task 3's assert-provided guard (Step 3), `SET AUTHREC` add loop (Step 6),
and `DISPLAY AUTHREC` verify (Step 7), substituting `gw_sec_svc_authrecs`,
`gw_sec_responder_principal`, and the SVCQM queue-manager variable
(`svc_qmgr`/`our_qmgr` per the role) for the target queue manager. The responder
side has no audit ledgers of ours, so the baseline-reconcile of Task 3 Step 5 is
not needed here; still verify the responder holds `+get` on the landing queue and
nothing beyond its two grants.

- [ ] **Step 4: Validate**

```bash
vrg-container-run -- vrg-validate
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope gw-sec-svc-queues \
  --message "enforce least-privilege authority grants for the responder on SVCQM" \
  --body "SET AUTHREC (epic #235): the responder (SVC.SVRCONN) gets get/inq on its landing queue and put on its reply target, nothing else. Declare-and-verify the effective principal; DISPLAY AUTHREC asserts the grant. Idempotent, owned."
```

---

## Task 5: Docs — "Security & permissions" section

**Files:**
- Modify: `docs/mq-native-settlement-tracking.md`
- Modify: `ansible/README.md`

**Interfaces:**
- Consumes: the model as implemented in Tasks 1–4.
- Produces: the human-facing description of the model + the ownership boundary.

**Context:** No setup/config doc currently describes object permissions. Add a
focused section to the settlement-tracking doc and a pointer from the Ansible
README. Content is generic; invented identity names only.

- [ ] **Step 1: Read the doc's current structure**

Read `docs/mq-native-settlement-tracking.md` headings to find the right insertion
point (after the deployment/observability material, before any appendix).

- [ ] **Step 2: Write the "Security & permissions" section**

Add a section covering, in prose + the §5 grant table from the spec:

- The least-privilege model: app `+put`/`+get`; observers `+browse +inq +dsp`;
  inbound MCA `+put`; responder its own grants — each identity and what it may
  and may not do.
- **Observer-identity separation:** every audit-queue reader (collector and
  exporter) connects as the dedicated `MON.SVRCONN`/`mon-client` monitoring
  identity; the business `APP.SVRCONN` identity carries **no browse right** on
  the history.
- **The ownership boundary:** we `SET AUTHREC` only on `GW.SEC.*` objects we own
  and point our own observability roles at the existing monitoring identity; we
  **define no channel and change no `CHLAUTH`/`MCAUSER`** — those are the base
  lab's, declare-and-verified.
- **Enforced, not conventional:** browse-only is now a queue-manager authority,
  proven by the 2035 denial test (reference the validation task).

- [ ] **Step 3: Add the README pointer**

In `ansible/README.md`, add a one-line pointer under the roles overview to the
new "Security & permissions" section, noting that `gw-sec-queues`/
`gw-sec-svc-queues` set the least-privilege authrecs and `gw-sec-observability`
connects as the observer identity.

- [ ] **Step 4: Validate**

```bash
vrg-container-run -- vrg-validate
```
Expected: PASS (markdownlint clean).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type docs --scope security \
  --message "document the least-privilege authority model and ownership boundary" \
  --body "New Security & permissions section (epic #235): the per-identity grant model, the observer-identity separation, and the self-owning-bolt-on boundary (we authrec our own objects; the lab owns the channels)."
```

---

## Operational Task A (deployment): apply the least-privilege model to the live lab

Filed with `vrg-issue-create --kind deployment`, **blocked-by Tasks 1–4**. Not
PR-workable; run with `issue-deploy`. Closes on `Outcome: SUCCESS`.

- **Precondition self-check:** Tasks 1–4 merged to develop; the `mon-client`
  keystore + the effective-principal variables available at apply time. If a
  required secret/var is unset, comment "blocked: preconditions not met" and stop.
- **Procedure:** apply the settlement plays from `site.yml` against the live lab
  (`gw-sec-queues`, `gw-sec-svc-queues`, `gw-sec-observability`,
  `gw-sec-collector`) with the principal + keystore variables supplied. The roles'
  own declare-and-verify + `DISPLAY AUTHREC` assertions gate the apply.
- **Acceptance:** the playbook completes green; `DISPLAY AUTHREC` shows the
  intended grants; the exporter + collector are running as the `MON.SVRCONN`
  identity.

## Operational Task B (validation): prove the model — positive paths + 2035 denials

Filed with `vrg-issue-create --kind validation`, **blocked-by Operational Task
A**. Not PR-workable; run with `issue-validate`. Closes on `Outcome: SUCCESS`.

- **Precondition self-check (blocks if unmet):** each effective identity
  (`MON.SVRCONN` observer, `APP.SVRCONN` app, inbound MCA) is **non-privileged
  and not a member of `mqm`** — else OAM bypasses the authrecs and no 2035 can
  fire. Discover via `DISPLAY CHSTATUS … MCAUSER` + group membership. If any is
  privileged, comment the blocked reason and **stop** (never fabricate a pass).
- **Positive paths:** app puts an instruction → streams to counterparty + retained;
  reply lands on `GW.SEC.REPLY` → streams to `GW.SEC.STATUS.AUDIT`; both observers
  (collector + reidentified exporter) browse both ledgers and their metrics
  advance. No regression.
- **No-wider-grant enumeration (spec §5):** `DISPLAY AUTHREC PROFILE('<each
  GW.SEC.* queue>') OBJTYPE(QUEUE) ALL` and confirm **no** entity other than the
  declared principals confers `+browse`/`+get`/`+put` on an audit ledger — i.e.
  the Task 3 Step 5 reconcile left no group/`+all` grant behind. A residual wider
  grant is a FAILURE (or, if lab-owned, a blocked declare-and-verify finding).
- **Negative denials (the load-bearing proof):** under the `MON.SVRCONN` observer
  identity, a destructive `MQGET` and an `MQPUT` on an audit ledger each return
  **MQRC 2035**; under the `APP.SVRCONN` app identity, an `MQOO_BROWSE` of an
  audit ledger returns **2035**. (These are the live confirmation of the
  enumeration above — the enumeration says *why*, the denial proves the *effect*.)
- **Boundary assertion:** the applied change touched only `GW.SEC.*` authrecs +
  our observability role's identity config; the lab's channels/`CHLAUTH` are
  unchanged.
- **Acceptance:** all positive paths green AND all three denials return 2035;
  record the evidence in the `Outcome: SUCCESS` comment.

---

## Self-Review

**Spec coverage:**
- Spec §1/§2 (separate observer from business identity) → Tasks 1, 2.
- Spec §5 grant table (app / observers / inbound MCA) → Task 3; responder → Task 4.
- Spec §5 invariants (per-operation authority; **no wider grant**) → Task 3
  Step 5 baseline-enumerate + reconcile (`AUTHRMV` forbidden authorities;
  stop-with-finding on lab-owned grants) + Step 7 dual `DISPLAY AUTHREC` verify
  (observer no get/put; app no browse) + Operational Task B enumeration and
  app-browse denial.
- Spec §3 boundary + Open Q1 (declare-and-verify identity) → Task 3 Steps 3–4;
  Global Constraints.
- Spec §3 non-privileged precondition → Operational Task B precondition.
- Spec §6 docs → Task 5.
- Spec §7 validation (positive + 2035 denials + boundary) → Operational Tasks A/B.
- Spec Open Q3 (alias authority) → Task 3 grants `+put` on both alias and base.

**Open items deferred to the plan's own choices (resolved):**
- Open Q1 (discovery timing) → declare-the-principal + verify-against-CHSTATUS.
- Open Q2 (principal vs group) → PRINCIPAL for the lab; the grant map's
  `principal` field can hold a group name with a `GROUP` objtype toggle if a
  production run prefers groups — noted in the Task 3 defaults comment.
- Open Q4 (inbound-MCA identity) → `gw_sec_mca_principal` var, granted `+put` on
  our `GW.SEC.REPLY` only; channel untouched.

**Placeholder scan:** Task 4 Step 2 intentionally carries `<landing-queue-var>`/
`<reply-target-var>` placeholders **because** the exact SVCQM object variable
names must be read from the role in Step 1 — the step says so explicitly. Every
other step carries real content.

**Type/name consistency:** variable names (`gw_sec_observer_principal`,
`gw_sec_app_principal`, `gw_sec_mca_principal`, `gw_sec_responder_principal`,
`gw_sec_authrecs`, `gw_sec_svc_authrecs`, `gw_sec_exporter_mon_client_p12/_pw`)
are used identically across defaults, tasks, and the operational tasks.
