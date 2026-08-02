# Scenario catalog

Seven deterministic settlement-failure episodes, each isolating on a bounded macro dimension so a rare, concentrated incident separates from the diffuse background of routine fails. The metric dump behind each page is the reproducible source of truth; the dashboard screenshot is captured live during validation.

| scenario | isolating dimension | expected reason family |
|---|---|---|
| [counterparty_outage](counterparty_outage.md) | `reason_family` | `matching` |
| [csd_incident](csd_incident.md) | `time_and_state` | `settlement` |
| [lack_of_securities](lack_of_securities.md) | `reason_family` | `settlement` |
| [stale_ssi](stale_ssi.md) | `reason_family` | `matching` |
| [rejection_burst](rejection_burst.md) | `reason_family` | `rejection` |
| [funding_squeeze](funding_squeeze.md) | `reason_and_msg_type` | `cash` |
| [silent_degradation](silent_degradation.md) | `reason_family_trend` | `matching` |
