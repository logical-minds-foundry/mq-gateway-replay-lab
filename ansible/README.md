# Ansible — gateway experiment queue provisioning

Provisions the experiment's own gateway objects on both ends of the counterparty
link: the **remote object model** (issue #56, proven live in #54) on our own
queue manager (`gw-queues`) and on the counterparty's queue manager, `SVCQM`
(`gw-svc-queues`). Nothing either box already owns is created or modified —
both roles declare-and-verify the queue manager (and, for the inter-QM link,
the EXISTING transmission queue + channel) and create only `GW.<counterparty>.*`
(parasite / boundary rule).

## What it creates

For counterparty tag `CPB` (the default):

### Our side — `gw-queues` (applied to our queue manager)

| Object | Type | Role |
|--------|------|------|
| `GW.CPB.REQUEST` | `QALIAS` → `GW.CPB.REQUEST.AUDIT` | The app's transparent put target — same name as before, so `gwlab` is unchanged |
| `GW.CPB.REQUEST.AUDIT` | `QLOCAL` | The retained-original outbound copy (the replay ledger); `STREAMQ`'s a `MUSTDUP` duplicate onward |
| `GW.CPB.REQUEST.REMOTE` | `QREMOTE` → `SVCQM` | The streamed duplicate's route to the counterparty's landing queue, over the EXISTING XMITQ/channel |
| `GW.CPB.REPLY` | `QLOCAL` | Confirmations arrive here from the counterparty; `STREAMQ`'s a `MUSTDUP` duplicate to its audit copy |
| `GW.CPB.REPLY.AUDIT` | `QLOCAL` | Inbound audit queue — the authoritative "processed" ledger |

### Their side — `gw-svc-queues` (applied to `SVCQM`)

| Object | Type | Role |
|--------|------|------|
| `GW.CPB.REQUEST` | `QLOCAL` | Our request lands here; the counterparty's responder drains it locally |
| `GW.CPB.REPLY` | `QREMOTE` → our queue manager | Routes the counterparty's reply back to our `GW.CPB.REPLY`, over the EXISTING return XMITQ/channel |

All `QLOCAL`s are `DEFPSIST(YES)` (persistent). The inter-QM transmission
queues and channels in both directions **already exist** — they are the lab's
inter-QM link — and are only declare-and-verified (a `DISPLAY` that fails
loudly if absent), never created or altered.

The **audit copies are produced by the queue manager**, not the app:
`REQUEST.AUDIT` and `REPLY` are configured `STREAMQ(...) STRMQOS(MUSTDUP)`, so
every message is atomically copied with the same MsgId and no application
logic (the primary audit copy; research `#54`). `MUSTDUP` gives the
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

`site.yml` runs three plays: `gw-queues` (our side), `gw-svc-queues` (their
side, `SVCQM`), and `gw-observability`. **Apply `gw-queues` and `gw-svc-queues`
as two separate `ansible-playbook` invocations, each with `--limit`** — both
roles use a `qmgr_name` var for the queue manager *that play* targets (our
queue manager vs. SVCQM), and `-e` extra-vars are a **global** override across
every play in one command, not play-scoped. Passing one `qmgr_name` for both
plays in a single invocation would silently point the second play at the
wrong box.

`gw-queues` must additionally target the **ACTIVE** Native-HA node
specifically (a passive node has no running queue manager to target); a
follow-up will auto-detect it, but for now pass it explicitly via
`lab_qm_host`.

From inside the shared lab VM, on/reaching the active queue-manager node:

```bash
cd ansible

# Our side — the active Native-HA node.
ansible-playbook -i inventory/lab.yml site.yml --limit gateway_qm \
  -e qmgr_name=<LAB_QM> -e lab_qm_host=<ACTIVE_NODE>

# Their side — SVCQM, a separate invocation (see the --limit note above).
ansible-playbook -i inventory/lab.yml site.yml --limit gateway_svc_qm \
  -e qmgr_name=<SVCQM_LIVE_NAME> -e our_qmgr=<LAB_QM> -e svc_qm_host=<SVCQM_HOST>

# Observability.
ansible-playbook -i inventory/lab.yml site.yml --limit gateway_obs \
  -e gw_exporter_qmgr=<LAB_QM> -e gw_exporter_conn=<CONN>  # see role defaults for the full var set
```

- `qmgr_name` is **required** for both `gw-queues` (our queue manager) and
  `gw-svc-queues` (SVCQM's live instance name) — a missing value fails loudly
  by design. `gw-svc-queues` additionally requires `our_qmgr` (our queue
  manager's name, used to route replies back and to name the return channel).
- Both plays are **idempotent**: every `DEFINE` uses `REPLACE`, so re-running
  converges each object to the same declared attributes rather than erroring on
  "already exists"; the declare-and-verify `DISPLAY`s never mutate anything.
- **Cold-rebuild safe**: after a lab rebuild, re-apply to re-establish the
  objects in one pass.

> The exact active-node host, queue-manager names, and SVCQM host are
> confirmed against the live 3+3 (and the counterparty) at apply time; set
> `lab_qm_host` / `qmgr_name` / `svc_qm_host` / `our_qmgr` accordingly.
