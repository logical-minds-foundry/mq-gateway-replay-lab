# Coverage & testing exceptions — audit

**Policy:** this repo enforces **100% branch + line coverage** (`vrg-validate`).
This document records every exception to that policy, why it exists, and its
disposition — so the scope of what we exempt is always explicit and small.

## 1. Coverage `omit` — whole-module exemptions

**There are none.** `[tool.coverage.run] omit` is empty: every module in `src/`
is covered to **100% branch + line**. The sections below record how the modules
that were once exempted were brought under coverage by *mocking the boundary* —
no source logic changes, no `pragma`, no faked coverage.

### Retired in #157 (the three pre-epic v1 box-only modules)

The last three whole-module exemptions — the pre-epic-#49 v1 modules — were
retired with the same boundary-mocking technique #129 established:

| Module | How it is now covered |
|---|---|
| `pymqi_messaging.py` | A **fake `pymqi` module** is injected into `sys.modules` before the adapter's lazy `import pymqi`, reproducing the MQI surface it reads (`CD`/`SCO`/`QueueManager`/`Queue`/`MD`/`GMO`/`PMO`, `MQMIError.reason`, a `CMQC` constant namespace). Each test scripts what `Queue.get` returns/raises, covering `__init__` (plaintext / TLS with & without `cert_label`), `_open` (cache miss + hit), `put` (explicit MsgId vs `MQPMO_NEW_MSG_ID` read-back), `get` (present / 2033→`None` / other→raise), `browse` + `browse_new` (BROWSE_FIRST→NEXT cursor, `limit` cap, 2033 stop, `MQRC_CONNECTION_BROKEN` reopen-at-first, other→raise), `commit`/`backout`/`clear`/`disconnect`, and `_put_time` (valid + unstamped). No MQ client libraries needed. |
| `exporter_server.py` | `do_GET` (`/metrics`, `/`, 404) driven against an in-memory `wfile` + stubbed `send_*`; `serve()` with `PyMqiMessaging` and `HTTPServer` patched so nothing connects or binds; `main()` argparse. |
| `deploy_http.py` | `urllib.request.urlopen` mocked by a URL-routing fake (Prometheus QM discovery, stack lookup, Grafana cockpit-folder resolution: found / no-stack / 404 / root, and the dashboard POST); `main()` against a faked `HttpDeployClient` covering both the deployed and the skipped-QM print branches. |

### Retired in #129 (the five epic-#65 box-only modules)

These were exempted while the streaming/live pieces were built ("verified on the
box, not by unit tests"). #129 brought them all to **100%** by *mocking the
boundary* — no source loop-logic changes, no `pragma`, no faked coverage:

| Module | How it is now covered |
|---|---|
| `swift/serve_http.py` | `do_GET` (`/metrics`, `/`, 404) driven against a fake `wfile` + stubbed `send_*`; `connect()` with `PyMqiMessaging` monkeypatched; `main()` argparse. |
| `swift/serve_responder.py` | drain/emit via the fake `Messaging`; `run()` loop broken by a `time.sleep` sentinel; `connect()`; `main()`. |
| `swift/gateway_run.py` | `drain_replies` with `time.time`/`time.sleep` scripted across the deadline; `emit_end_states` both render branches; `run()`; `main()`. |
| `swift/deploy_http.py` | `urllib.request.urlopen` mocked (datasource discovery: default / fallback / none) + POST; `main()` with a faked `HttpDeployClient`. |
| `swift/stream/serve_collector.py` | handler as above; `run()` loop with `sweep` scripted `more=True`→`False` (both branches); `HTTPServer`/`threading.Thread` patched so nothing binds/spawns; `main()`. |

**Technique:** every external boundary is simulated — the lazy `pymqi` import, the
bound socket / `HTTPServer`, `threading`, `time.sleep`/`time.time`, and `urllib`.
The live end-to-end proofs on the box are *retained*; the unit coverage is added on
top, not in place of them.

### Remaining

**None.** `#157` retired the last three (see above). The repo now has **zero**
whole-module coverage exemptions — including `pymqi_messaging.py`, the one true
boundary, which is covered by injecting a fake `pymqi` rather than needing the MQ
client libraries. The live end-to-end proofs on the box (the two-process
last-hour run, the remote harnesses under `demo/proofs/`) are **retained on top
of** the unit coverage, not replaced by it.

## 2. `exclude_lines` — conventional non-logic excludes (kept)

`[tool.coverage.report] exclude_lines`:

- `if __name__ == .__main__.` — entry-point guard.
- `if TYPE_CHECKING:` — type-only imports (no runtime).
- `: \.\.\.$` — `Protocol` method stubs.

These exclude **non-executable / non-logic** lines, not behavior. Standard practice;
they are not real exemptions and are retained.

## 3. `# pragma: no cover`

**None** in `src/`. There are no inline coverage suppressions — coverage is proven
by tests, not by silencing lines.

## 4. Lint / type suppressions (for scope — *not* coverage/testing exemptions)

Recorded for completeness (the user asked for the full scope of what we exempt).
These are code-quality suppressions, each narrow and justified, and do **not** reduce
test or coverage rigor:

- `# noqa` — 6 files, by rule: `S310` (×6, `urllib` open of *internal* Grafana/Prometheus
  URLs), `S311` (×3, non-crypto `random` in the simulation generator), `N802` (×3,
  `do_GET` — the `http.server` dispatch name), `G002` (×3, logging), `A002` (×3, a
  parameter named like a builtin).
- `# type: ignore` — **1**: `pymqi_messaging.py` `import pymqi` (pymqi ships no type stubs).

## 5. Summary

| Exemption class | Before #129 | After #129 | After #157 |
|---|---:|---:|---:|
| Omitted modules | 8 | 3 (all pre-epic v1) | **0** |
| — of which epic-#65 | 5 | 0 | 0 |
| `# pragma: no cover` | 0 | 0 | 0 |
| `exclude_lines` (conventional) | 4 | 4 | 4 |

**Net:** the repo now has **zero** whole-module coverage exemptions — every line
of every `src/` module is covered to 100% branch + line, the pymqi adapter
included (via an injected fake `pymqi`). The only entries left in this document
are the conventional `exclude_lines` (non-executable guards) and the narrow
lint/type suppressions — neither reduces test or coverage rigor.
