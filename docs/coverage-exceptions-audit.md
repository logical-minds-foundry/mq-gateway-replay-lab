# Coverage & testing exceptions — audit

**Policy:** this repo enforces **100% branch + line coverage** (`vrg-validate`).
This document records every exception to that policy, why it exists, and its
disposition — so the scope of what we exempt is always explicit and small.

## 1. Coverage `omit` — whole-module exemptions

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

### Remaining (pre-existing, v1 / epic #49-era) — not yet retired

| Module | Why it's still omitted | Path to retire |
|---|---|---|
| `pymqi_messaging.py` | The **actual pymqi adapter** — the one true boundary; needs the MQ client libs to *run*. | Coverable by mocking the whole pymqi surface (`CD`/`QueueManager`/`Queue`/`MD`/`GMO`/`PMO`) and asserting the adapter drives them correctly. |
| `exporter_server.py` | v1 exporter HTTP server (socket + pymqi). | Same boundary-mocking pattern proven in #129. |
| `deploy_http.py` | v1 Grafana deploy (HTTP). | Mock `urllib`, as the swift `deploy_http` now does. |

**Recommendation:** retire all three in a follow-up pass with the #129 pattern.
`pymqi_messaging.py` is the only module where the exemption is *arguably* principled
(it wraps the boundary itself), but it is still mockable and should be covered.

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

| Exemption class | Before #129 | After #129 |
|---|---:|---:|
| Omitted modules | 8 | **3** (all pre-epic v1) |
| — of which epic-#65 | 5 | **0** |
| `# pragma: no cover` | 0 | 0 |
| `exclude_lines` (conventional) | 4 | 4 |

**Net:** every line of this epic's code is now covered. The only remaining coverage
exemptions are the three pre-#65 v1 modules — a clean, bounded follow-up. The lint/type
suppressions are narrow code-quality accommodations, not gaps in what the tests touch.
