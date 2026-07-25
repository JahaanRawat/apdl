# Audit reports

Chronological record of APDL's release audits and their remediation tracking.
Filenames carry the audit date. Reports are historical evidence — they describe
the audited revision and are not rewritten after remediation; current
per-finding status lives in the matching findings register or crosswalk.

| Date | Document | What it is |
|---|---|---|
| 2026-07-13 | [oss-release-bug-audit-2026-07-13.md](oss-release-bug-audit-2026-07-13.md) | Full OSS release-readiness audit of `recent-critical-fixes` @ `d095776` (historical snapshot, no-go) |
| 2026-07-13 | [oss-release-minimum-fixes-2026-07-13.md](oss-release-minimum-fixes-2026-07-13.md) | Minimum developer-preview blocker list derived from the 2026-07-13 audit, with implementation status |
| 2026-07-16 | [oss-release-unqualified-reaudit-2026-07-16.md](oss-release-unqualified-reaudit-2026-07-16.md) | Independent post-remediation re-audit of the fix stack tip `ddd79d0` (NO-GO; findings RA-01–RA-18) |
| 2026-07-16 | [reaudit-2026-07-16-findings-register.md](reaudit-2026-07-16-findings-register.md) | Per-finding remediation tracker for RA-01–RA-18 — update statuses here as fixes merge |
| 2026-07-22 | [oss-release-current-branch-audit-2026-07-22.md](oss-release-current-branch-audit-2026-07-22.md) | Current-branch implementation/release audit at `67a7e6c` (NO-GO; namespaced findings `OSS-2026-07-22:C-01`–`H-17`) |
| 2026-07-23 | [oss-release-high-findings-crosswalk-2026-07-23.md](oss-release-high-findings-crosswalk-2026-07-23.md) | Source and commit crosswalk for the distinct 18 follow-up High findings remediated by PR #125 |
