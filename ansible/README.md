# Ansible — gateway experiment queue provisioning

Adds the experiment's **own** four queues to the lab's **running** queue manager,
so the `gwlab` demo has somewhere to put and get. Nothing the lab owns is created
or modified — the role declare-and-verifies the queue manager and creates only
`GW.<counterparty>.*` (parasite / boundary rule).

## What it creates

For counterparty tag `CPB` (the default):

| Queue | Role |
|-------|------|
| `GW.CPB.REQUEST` | Outbound to the (simulated) counterparty; the responder drains it |
| `GW.CPB.REQUEST.AUDIT` | Outbound audit queue — the replay copy |
| `GW.CPB.REPLY` | Confirmations arrive here from the responder |
| `GW.CPB.REPLY.AUDIT` | Inbound audit queue — the authoritative "processed" ledger |

All four are `QLOCAL`, `DEFPSIST(YES)` (persistent). This is the **self-contained
single-queue-manager demo topology**: the responder plays the counterparty
locally, so no channels or remote queue-manager wiring is touched.

The **audit copies are produced by the queue manager**, not the app: `REQUEST`
and `REPLY` are configured `STREAMQ(<their>.AUDIT) STRMQOS(MUSTDUP)`, so every
message is atomically copied to its audit queue with the same MsgId and no
application logic (the primary audit copy; research `#54`). `MUSTDUP` gives the
both-or-neither guarantee — if the copy cannot be written, the original put
fails. The app-level two-put / get-copy in `gwlab` is the documented fallback for
queue managers where streaming is not configured.

## Retention & sizing

The role also sets, on the audit queues:

- **`CAPEXPRY(864000)`** — a rolling **24h** cap (tenths of a second). The
  streamed copy starts `MQEI_UNLIMITED`, so the cap becomes its TTL; expired
  copies are removed by the periodic reconcile browse (or the qm.ini
  `ExpiryInterval` scanner). This **replaces** the start-of-day / end-of-day
  clearing — the queue manager bounds the audit queue. Verified live (issue `#27`).
- **`MAXDEPTH(999999999)`** — the default `5000` is far too small for a day of
  audit traffic at scale. The single queue-file limit is *not* a concern:
  `MAXFSIZE(DEFAULT)` is ~2 TB. `MAXMSGL` stays the 4 MB default (ample for
  ~200 KB messages). Size `/var/mqm` for a day of audit traffic on each Native
  HA node.

## Apply (against the running lab)

From inside the shared lab VM, on/reaching the active queue-manager node:

```bash
cd ansible
ansible-playbook -i inventory/lab.yml site.yml -e qmgr_name=<LAB_QM>
```

- `qmgr_name` is **required** — a missing value fails loudly by design.
- The play is **idempotent**: re-running is a no-op (existing queues report
  "already defined", `rc 10`, and are left as-is).
- **Cold-rebuild safe**: after a lab rebuild, re-apply to re-establish the queues
  in one pass.

> The exact active-node host and queue-manager name are confirmed against the
> live 3+3 at apply time; set `lab_qm_host` / `qmgr_name` accordingly.
