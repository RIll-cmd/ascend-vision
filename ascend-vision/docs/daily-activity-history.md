# Daily activity history V1

## What is stored

Daily activity history is a separate opt-in from current laptop context, proactive coaching, phone sharing, approved memories, and chat. It starts off. When enabled, Vision writes only local-day aggregate seconds and correction records to `activity_history.db` beside the configured session database. It does not write raw observation events, app titles, screenshots, webcam frames, audio, or chat transcripts.

The dashboard can enable or pause new aggregation, export retained summaries as JSON, record a correction that removes disputed minutes from a metric, and delete all saved summaries and corrections. Pausing retains existing aggregates; deleting removes them. New data automatically expires after 30 local calendar days. Scoring is disabled and has no enable control in this first slice.

Collection requires both the explicit activity-history toggle and the existing laptop current-context feature to be enabled. It only accounts for short intervals sampled while Vision is running. Unknown, stale, unavailable, and paused signals reduce per-metric coverage; gaps over five seconds and wall/monotonic clock jumps are dropped instead of imputed. Durations split at local midnight and use UTC/monotonic agreement to avoid inventing elapsed time during suspend or clock correction.

Metrics are intentionally separate: tracked focus-session minutes, declared break minutes, and observed entertainment-category minutes. They can overlap and must not be summed as total productive time. “Uncovered” means Vision was running for that tracked interval but did not have eligible evidence for that metric; it is not a negative score or behavior judgment. Only the selected application category is retained, and entertainment category time additionally requires a fresh desktop activity sample.

## Core and score boundaries

Daily mission outcomes come only from Core's owner-authorized `mission_completion_history` contract, which supplies stable mission IDs and completion timestamps for the requested timezone-aware local-day interval. Vision validates and deduplicates those rows; it never infers completion from current status, XP, or an agent's idle state. Reopened or deleted missions are not presented as currently completed. If the authenticated Core read is unavailable, the summary says outcomes could not be verified rather than fabricating them. The contract is implemented in the inspected Vision/Core working copies, but authenticated staging integration and real-dataset validation remain outstanding.

The dashboard returns `score: null` and `score_enabled: false`. It does not ask the LLM to calculate or rewrite totals. A future score requires a separate explicit enable control, a visible formula/inputs/coverage treatment, and deterministic tests before it may be shown.

## Verification and known limits

- Automated tests cover default-off storage, explicit opt-in, interval union accounting, evidence expiry, paused/unknown coverage, local midnight, DST fall-back, clock corrections, long gaps, correction audit records, export, delete, and 30-day retention.
- The existing Windows context sampler runs only when `companion_context.enabled` is true. The independent dashboard can keep showing existing totals while new aggregation is paused.
- This is local application-storage deletion, not a promise about filesystem backups or snapshots.
- No deployment, staging-database test, or physical-device test is part of this phase.
