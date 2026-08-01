# Operating the lab from a dev session — access runbook

**Purpose.** A concise, repeatable path for reaching the shared lab's queue
managers from a development session, so no future session repeats the
access-discovery confusion. This is the operational access used for the live
experiments recorded in `docs/mqmd-streaming-fidelity.md` §1.4/§1.5 and the risk
register (issues #108, #187).

**Boundary reminder (parasite rule).** This runbook is about **operational
access** to a lab we share — browsing queues, putting probe messages, reading
reports. That is fine and expected. The parasite rule is about the gateway
**code**: this repo configures only what it owns and **declare-and-verifies** what
it doesn't; it never mutates the lab's config or restarts the lab's QM, and the
lab must not be made to *know about* this repo. Operating the box to run a
browse-only experiment does not cross that line; changing the lab's objects would.
Every run below is **browse / `DISPLAY` / probe-put only** — a failed precondition
is a loud finding, never a silent fix.

**Anonymization.** All names here are generic or lab-internal: `NHAUAPP` /
`SVCQM` / `GW.SEC.*` are invented; the `10.50.0.x` addresses and the Vagrant
insecure key are lab-internal and not client-identifiable. Keep it that way.

---

## 1. The VMs are libvirt guests under `qemu:///system`

The single most common confusion: the lab VMs are **not** under the default
libvirt *session* URI. They are **system** libvirt guests. List them with the
system connection explicitly:

```sh
virsh -c qemu:///system list --all
```

(If you use the default `qemu:///session`, you will see nothing and wrongly
conclude the lab is down.)

Do not drive the guests through raw `virsh` for lifecycle. Operate them through
the lab repo's own CLI, **`mqlab`**, which lives in that repo's virtualenv:

```sh
# from the mq-resiliency-lab-for-linux checkout
mq-resiliency-lab-for-linux/.venv/bin/mqlab vm inventory   # the node map (names ↔ IPs ↔ roles)
mq-resiliency-lab-for-linux/.venv/bin/mqlab vm roster      # roster view of the nodes
mq-resiliency-lab-for-linux/.venv/bin/mqlab status         # queue-manager / cluster status
mq-resiliency-lab-for-linux/.venv/bin/mqlab bootstrap      # bring the lab up (lab-owned)
mq-resiliency-lab-for-linux/.venv/bin/mqlab teardown       # take the lab down (lab-owned)
```

`mqlab` and the HA/DR `validate` framework are the **lab's** tooling — use them to
*operate and inspect*, but this repo ships its own CLI (`gwlab`) and never reuses
the lab's tooling for its own logic.

---

## 2. The node map and SSH

All nodes sit on **`10.50.0.0/24`**:

| Node | IP | Role |
|---|---|---|
| a1 | `10.50.0.11` | Gateway QM `NHAUAPP` Native HA node |
| a2 | `10.50.0.12` | Gateway QM `NHAUAPP` Native HA node |
| a3 | `10.50.0.13` | Gateway QM `NHAUAPP` Native HA node |
| svc-sim | `10.50.0.50` | Counterparty QM `SVCQM` |
| app-client | `10.50.0.60` | MQ client host (pymqi, TLS keystore) |

SSH in as **`vagrant`** with the Vagrant insecure key; `sudo` is enabled:

```sh
ssh -i ~/.vagrant.d/insecure_private_key vagrant@10.50.0.11
```

**Host keys churn on every rebuild**, so a normal `ssh` will trip
`REMOTE HOST IDENTIFICATION HAS CHANGED`. For lab nodes, disable host-key checking
(these are throwaway lab guests, not production hosts):

```sh
ssh -i ~/.vagrant.d/insecure_private_key \
    -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
    vagrant@10.50.0.11
```

---

## 3. Which node hosts the gateway QM (`NHAUAPP` is Native HA)

`NHAUAPP` is a **Native HA** queue manager spread across a1/a2/a3 — only **one**
node is active at a time. Find the active node with `dspmq` (run as any node's
`vagrant`, `sudo`):

```sh
ssh -i ~/.vagrant.d/insecure_private_key \
    -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
    vagrant@10.50.0.11 'sudo dspmq -o nativeha -x'
```

Run it against a1/a2/a3 until you find the one reporting the QM **running** as the
active instance. The counterparty QM `SVCQM` is single-instance on **svc-sim
(`10.50.0.50`)**.

---

## 4. Admin and browsing — run as the `mqm` user

MQ admin commands must run as **`mqm`**. Pipe `runmqsc` scripts and browse queues
non-destructively with `amqsbcg`:

```sh
# DISPLAY the gateway queues (declare-and-verify; never ALTER the lab's objects)
printf "DISPLAY QLOCAL(GW.SEC.*)\n" | ssh vagrant@<active-node-ip> 'sudo -u mqm runmqsc NHAUAPP'

# Browse a queue non-destructively (full MQMD dump)
ssh vagrant@<active-node-ip> 'sudo -u mqm /opt/mqm/samp/bin/amqsbcg GW.SEC.INSTRUCT.AUDIT NHAUAPP'

# Browse the streamed copy as the counterparty received it, on SVCQM
ssh vagrant@10.50.0.50 'sudo -u mqm /opt/mqm/samp/bin/amqsbcg <SVCQM_LANDING_QUEUE> SVCQM'
```

(Add the SSH `-i` / `-o` flags from §2 to each command; omitted above for brevity.)

`amqsbcg` is the workhorse for the fidelity experiments — it browses without
consuming and prints every MQMD field on its own labelled line, so the
original-vs-streamed diff (§5 of the fidelity doc) is a straightforward field
compare.

---

## 5. Custom MQMD / report options / groups / properties — use pymqi on the app-client

`amqsput` can put a plain message but **cannot** set report options, build a
grouped MQMD v2 message, or attach named properties. For those — i.e. the R4/R5/R6
experiments — put the message with **pymqi** on the **app-client (`10.50.0.60`)**,
client-connecting to `NHAUAPP` over the **`APP.SVRCONN`** channel with **mutual
TLS**. The app-client already carries the Python venv and the TLS keystore:

- Python: `/home/vagrant/mqvenv/bin/python`
- Key repository: `/home/vagrant/ssl/key` (stem — MQ appends `.kdb`)
- Client certificate label: `app-client`
- Channel: `APP.SVRCONN`, cipher `ANY_TLS13_OR_HIGHER`
- Connection name: all three Native HA nodes, comma-separated, so the client
  reaches whichever is active — `10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)`

### Minimal pymqi connect + put

```python
# /home/vagrant/mqvenv/bin/python, on app-client (10.50.0.60)
import pymqi

QMGR    = b"NHAUAPP"
CHANNEL = b"APP.SVRCONN"
CONNAME = b"10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)"

cd = pymqi.CD()
cd.ChannelName    = CHANNEL
cd.ConnectionName = CONNAME
cd.ChannelType    = pymqi.CMQC.MQCHT_CLNTCONN
cd.TransportType  = pymqi.CMQC.MQXPT_TCP
cd.SSLCipherSpec  = b"ANY_TLS13_OR_HIGHER"

sco = pymqi.SCO()
sco.KeyRepository    = b"/home/vagrant/ssl/key"     # -> /home/vagrant/ssl/key.kdb
sco.CertificateLabel = b"app-client"

qmgr = pymqi.QueueManager(None)
qmgr.connect_with_options(QMGR, cd=cd, sco=sco)

# Example: a probe with COA+COD report options and an explicit ReplyToQ (R6).
# For R4 set md.Version=MQMD_VERSION_2 + GroupId/MsgSeqNumber/MsgFlags;
# for R5 set md.Format=MQFMT_RF_HEADER_2 and prepend an MQRFH2 to the body.
md = pymqi.MD()
md.Report      = pymqi.CMQC.MQRO_COA | pymqi.CMQC.MQRO_COD
md.ReplyToQ    = b"GW.SEC.PROBE.REPORT"
md.ReplyToQMgr = b"NHAUAPP"

pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_NEW_MSG_ID | pymqi.CMQC.MQPMO_SYNCPOINT)
q = pymqi.Queue(qmgr, b"GW.SEC.INSTRUCT")           # the QALIAS
q.put(b"PROBE-PAYLOAD", md, pmo)
qmgr.commit()

# Capture the original MsgId to correlate against the streamed copy / any report.
print("MsgId:", md.MsgId.hex())

q.close()
qmgr.disconnect()
```

After the put, browse the streamed copy on the `SVCQM` landing queue (§4) and,
for R6, browse the `ReplyToQ` (`GW.SEC.PROBE.REPORT`) for the report — match on
`CorrelId == original MsgId`, read `Feedback` (`259` = `MQFB_COA`) and
`PutApplName` (the **generating** QM) to tell a local COA from a counterparty one.

---

## 6. Quick reference

| Need | Command |
|---|---|
| See the lab guests | `virsh -c qemu:///system list --all` |
| Node map / status | `…/.venv/bin/mqlab vm inventory` · `mqlab status` |
| SSH to a node | `ssh -i ~/.vagrant.d/insecure_private_key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no vagrant@10.50.0.<n>` |
| Find active `NHAUAPP` node | `sudo dspmq -o nativeha -x` on a1/a2/a3 |
| DISPLAY gateway queues | `printf "DISPLAY QLOCAL(GW.SEC.*)\n" \| sudo -u mqm runmqsc NHAUAPP` |
| Browse a queue (MQMD dump) | `sudo -u mqm /opt/mqm/samp/bin/amqsbcg <QUEUE> <QM>` |
| Custom MQMD / reports / groups / props | pymqi on app-client (`10.50.0.60`), `APP.SVRCONN` mutual TLS |

**Nodes:** a1/a2/a3 = `10.50.0.11/12/13` (`NHAUAPP` Native HA) · svc-sim =
`10.50.0.50` (`SVCQM`) · app-client = `10.50.0.60`.
