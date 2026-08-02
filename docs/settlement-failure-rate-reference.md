# Settlement-failure-rate calibration reference (data vs judgment)

**Epic:** #175 · **Task:** #190 (calibration reference doc)

This is the **calibration source of truth** for the realistic settlement-outcome
mix. Every per-asset-class rate the generator uses (epic #175, Task 3
`REALISTIC_BOOKS`) traces back to an anchor recorded here. The document exists to
keep two things apart, on purpose:

- **DATA** — what a cited public source actually reports, with the URL preserved
  so it can be independently verified.
- **JUDGMENT** — our reasoning on top of the data: interpolation, mapping a
  foreign market's figure onto our synthetic book, and the one rate for which
  **no public figure exists at all**.

Nothing here is client- or employer-specific. This is a *generic trading
back-office gateway* calibration problem; all books are invented, plausible
mixes anchored to public market-infrastructure research.

## 1. Why calibrate at all

The mechanism-demonstration generator (`MECHANISM_MIX`, today's `DEFAULT_MIX`)
is deliberately failure-heavy: it exists to exercise the replay/reconciliation
machinery, so it manufactures exceptions at a rate no real settlement day would
show. That teaching mix is fine for proving the mechanism, but it makes the
observability dashboards lie about what a *normal* day looks like — a mostly-green
stream with a thin tail of exceptions.

The realistic books below re-anchor the outcome mix to sourced, real-world
settlement-failure levels so the observability work (deviation scoring, board
registers, scenario injection) is calibrated against a plausible baseline rather
than an inflated one.

## 2. DATA — sourced anchors

Each row is a figure a public source reports. The **anchor** column is the value
we carry into calibration; the source text is quoted/paraphrased faithfully.

| Segment | Sourced figure (DATA) | Anchor | Source |
|---------|----------------------|--------|--------|
| Government / sovereign bonds ("govvie") | Sovereign fail rate **~2%** | ~2% fail | ESMA T+1 Report (2024) |
| Equity — US | CNS fail **2.12%**, non-CNS **3.31%** | ~2–3% fail | SIFMA/ICI/DTCC T+1 After-Action Report |
| Equity — EU | EEA all-asset fail **7.14%**; equity component **~5%** | ~5% fail | ESMA T+1 Report (2024) |
| ETF | ETF fail **17–20%** | ~17–20% fail | ESMA T+1 Report (2024) |
| Matching-rooted fails | **~27% of all fails** are matching-rooted | a few % of the stream | ICMA ERCC settlement-efficiency paper |
| Affirmation improvement (context) | Affirmation **73% → ~95%** post-T+1 | context only | SIFMA/ICI/DTCC After-Action Report |

### Sources (full URLs preserved for verification)

- **ESMA — Report on shortening the settlement cycle (T+1), Nov 2024.**
  Per-asset-class fail rates: 7.14% EEA all-asset; sovereign ~2%; ETF 17–20%.
  https://www.esma.europa.eu/sites/default/files/2024-11/ESMA74-2119945925-1969_Report_on_shortening_settlement_cycle.pdf
- **SIFMA / ICI / DTCC — T+1 After-Action Report.**
  US equity CNS fail 2.12% / non-CNS 3.31%; affirmation 73% → ~95%.
  https://www.sifma.org/news/press-releases/sifma-ici-and-dtcc-release-t1-after-action-report-industry-coordination-led-to-successful-transition-reducing-risk-and-costs-in-the-system
- **ICMA ERCC — "Optimising settlement efficiency."**
  27% of fails are matching-rooted.
  https://www.icmagroup.org/assets/Uploads/ERCC-discussion-paper-on-settlement-efficiency.pdf
- **iotafinance — MT548 message reference.**
  Reason-code catalog (`LACK` / `CLAC` / `MONY` / `CMON`; `Dxxx`; `NARR`).
  https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html

## 3. JUDGMENT — how the anchors map to calibrated books

The DATA above is per-market and per-asset-class; our synthetic aggregate is one
blended back-office day. The following are **our judgments**, not sourced facts:

- **Govvie ~2% fail.** Direct read of the ESMA sovereign figure. Judgment: the
  sovereign anchor is the closest public analog to our government-bond book.
- **Equity ~2–3% (US) vs ~5% (EU).** Two sources bracket equity. Judgment: we
  calibrate our equity book toward the lower US-institutional end (~2.8% fail),
  because the DTCC figures are the closest public analog to the institutional
  MT540–548 flow we model (see caveats). The EU ~5% is retained as the upper
  bound, not the anchor.
- **ETF ~17–20% fail.** Direct read of the ESMA ETF figure; we anchor at the low
  end (~18%). Judgment: ETF settlement is structurally fail-prone (creation/
  redemption mechanics), so a high fail share is expected and left intact.
- **Matching as a share of the stream.** DATA says 27% *of fails* are matching-
  rooted — a share *of fails*, not of the stream. Judgment: applied to a
  low-single-digit aggregate fail rate, matching-rooted exceptions land at ~a
  few percent of the whole stream; we split the fail reasons accordingly rather
  than inventing a standalone matching rate.
- **Rejection rate <<1% — JUDGMENT-ONLY.** **No public figure exists** for the
  outright validation-rejection rate of settlement instructions. We set it to a
  small fraction of a percent (~0.3–0.5%) purely as reasoning: rejections are the
  rarest exception family in a mature back office. This is the one number in the
  whole calibration that is **not** anchored to any source — flagged loudly so it
  is never mistaken for DATA.

### Resulting calibrated books (for cross-reference)

The generator's `REALISTIC_BOOKS` (epic #175, Task 3) realizes these judgments as
`clean / reject / fail / slow` weights. Reproduced here only so the doc and the
code cite one source of truth:

| Book | clean | reject | fail | slow | Anchored to |
|------|-------|--------|------|------|-------------|
| govvie | 0.965 | 0.003 | 0.020 | 0.012 | ESMA sovereign ~2% |
| equity | 0.955 | 0.005 | 0.028 | 0.012 | DTCC US CNS/non-CNS ~2–3% |
| etf | 0.800 | 0.005 | 0.180 | 0.015 | ESMA ETF ~17–20% |

Book share of the aggregate stream (invented, plausible back-office mix):
govvie 0.45 / equity 0.45 / etf 0.10. The blended clean share stays high
(mostly-green), which is the realistic-mode invariant.

## 4. Reason-code families (from iotafinance MT548)

The exception families the classifier buckets responses into map to MT548 reason
codes as follows (source: iotafinance MT548 reference, above):

| Family | MT548 reason codes | Meaning |
|--------|--------------------|---------|
| Settlement | `LACK`, `CLAC` | Lack of securities to deliver |
| Cash | `MONY`, `CMON` | Lack of cash to pay |
| Matching | `Dxxx` (e.g. `DSEC`, `DDEA`, `DTRD`) | Unmatched / disagreement between parties |
| Rejection | `NARR` / format / validation | Instruction rejected on validation |

These families are the macro, non-sensitive buckets used downstream by the
reason-family classifier (Task 2) and the scenario harness (Task 11). Asset class
shapes the *aggregate outcome mix* generation-side only; it is never carried on
an individual instruction (the macro-only invariant).

## 5. Honesty caveats

These are load-bearing. Read them before quoting any figure above.

- **ESMA changed measurement methodology in 2022.** Comparisons across the
  break are not like-for-like. Cite ESMA figures as **trends** (the relative
  ordering — ETF >> equity > sovereign), **not as precise absolute levels**. The
  anchors are calibration targets, not published constants.
- **The rejection rate is JUDGMENT-ONLY.** No public figure exists; the
  <<1% value is our reasoning alone (see §3). Never present it as sourced.
- **DTCC figures are institutional-equity.** The US CNS/non-CNS fail rates are
  the closest public analog to the institutional MT540–548 flow we model, but
  they are an equity-market proxy, not a measurement of our synthetic book. We
  use them because they are the best available public anchor, and we say so.
- **Anchors are invented books, plausibly calibrated.** The books and their
  aggregate shares are synthetic. Their *shape* is anchored to the sources; their
  exact weights are ours. Nothing here is client- or employer-derived.
