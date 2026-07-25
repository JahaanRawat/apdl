# PR #125 follow-up High-finding crosswalk

- **Source date:** 2026-07-23
- **Source PR:** [#125 — release: close all 18 High findings from the OSS audit](https://github.com/kuvera-apdl/apdl/pull/125)
- **Source head:** `77997e3f03a87a9d053de0d0355772a7f22a3772`
**Status:** implementation evidence on an open cumulative branch; merge and
independent re-audit remain required.

## Identifier authority

The historical
[2026-07-22 current-branch audit](oss-release-current-branch-audit-2026-07-22.md)
defines two Critical findings and seventeen High findings. Their stable
identifiers are `OSS-2026-07-22:C-01`–`C-02` and
`OSS-2026-07-22:H-01`–`H-17`.

PR #125 defines a different set of eighteen High findings discovered while
reviewing the cumulative remediation stack. Their stable identifiers are
`PR125:H-01`–`PR125:H-18`. Reusing the display labels `H-01`, and the phrase
“from the OSS audit,” did not make the two sets equivalent. This crosswalk is
the authority for that distinction and binds every PR #125 identifier to its
source relationship and implementation commit.

## The eighteen PR #125 findings

| Stable ID | Follow-up finding | Relationship to earlier finding evidence | Implementation commit |
|---|---|---|---|
| `PR125:H-01` | Browser identity and session state were persisted while consent was denied | New follow-up finding in the JavaScript SDK privacy surface; not `OSS-2026-07-22:H-01` | [`85bf423`](https://github.com/kuvera-apdl/apdl/commit/85bf42374a31e276315b2dc933c99a178b7d84ee) |
| `PR125:H-02` | Durable offline events were deleted before server acknowledgement | New follow-up finding in SDK delivery durability | [`6d02f25`](https://github.com/kuvera-apdl/apdl/commit/6d02f25f7848898b6827f7fee8bf898965500455) |
| `PR125:H-03` | Offline eviction was silent and delivery reports overstated persistence | New follow-up finding in SDK delivery accounting | [`71f04c4`](https://github.com/kuvera-apdl/apdl/commit/71f04c4e93451a662e8c9c006627e7740d7ae230) |
| `PR125:H-04` | Page-unload delivery was not lifecycle-safe | Elevates and completes the unload/keepalive issue recorded under the JavaScript SDK section of the 2026-07-22 audit | [`b5509bb`](https://github.com/kuvera-apdl/apdl/commit/b5509bb47ad4e5e5dab5dc9efbf9729e76513dd5) |
| `PR125:H-05` | Client timestamps could bypass retention and differed across ingestion paths | New follow-up to the audit's storage/retention assessment; establishes receipt-time authority | [`6bd5238`](https://github.com/kuvera-apdl/apdl/commit/6bd5238b903f4f971cabd247a26ba406827f965e), [`7411b7f`](https://github.com/kuvera-apdl/apdl/commit/7411b7f2b57085303b5fc254489cb04dda6ff9da) |
| `PR125:H-06` | Personally attributable derived analytics had no coherent retention or deletion path | Direct implementation evidence for `RA-14` in the 2026-07-16 register | [`97fe46c`](https://github.com/kuvera-apdl/apdl/commit/97fe46cda64dc7b373fdfe3f40661715d1e9e0a7) |
| `PR125:H-07` | Unbounded experiment weights crashed Python and diverged from JavaScript | New follow-up strict-contract finding | [`c997298`](https://github.com/kuvera-apdl/apdl/commit/c997298a5394629336c12e97e68f721edfa18f66) |
| `PR125:H-08` | Anonymous browser users could not enroll in authored experiments | New follow-up to the canonical enrollment work for `OSS-2026-07-22:C-01`/`C-02`; it adds explicit identity authority rather than renumbering either Critical finding | [`77997e3`](https://github.com/kuvera-apdl/apdl/commit/77997e3f03a87a9d053de0d0355772a7f22a3772) |
| `PR125:H-09` | Completeness evidence contradicted the declared four-writer topology | Follow-up architectural correction to `OSS-2026-07-22:H-02` and PR #124 item 12 | [`30e7682`](https://github.com/kuvera-apdl/apdl/commit/30e76821ac81b61858f0435417a895489f650d47) |
| `PR125:H-10` | One tenant could starve every later boundary marker | Follow-up fairness/failure-isolation defect in the `OSS-2026-07-22:H-02` completeness subsystem | [`ff3e1c1`](https://github.com/kuvera-apdl/apdl/commit/ff3e1c1a2e265cf9605780d8e8b9e8c82b28b380) |
| `PR125:H-11` | The default Codegen worker command could not start | New exact-runtime finding in the release path | [`f991333`](https://github.com/kuvera-apdl/apdl/commit/f991333d218b14b17ce89a267422db389aa59c68) |
| `PR125:H-12` | Repository build code ran in the same worker that held provider credentials | Completes the process-isolation boundary required by `OSS-2026-07-22:H-08` | [`6365dcf`](https://github.com/kuvera-apdl/apdl/commit/6365dcf41c1120cb7d0025d68e2d55b1b4e4b90e) |
| `PR125:H-13` | Binary Codegen changes bypassed secret scanning | Extends the canonical detector work for `OSS-2026-07-22:H-07` to exact changed blobs | [`4b68c4e`](https://github.com/kuvera-apdl/apdl/commit/4b68c4ef9d4f1ff6c01491bb63c16ee5b5901e02) |
| `PR125:H-14` | The published Codegen worker failed the strict vulnerability gate | Completes dependency-audit evidence associated with `OSS-2026-07-22:H-15` and its release-engineering gap | [`ab1815e`](https://github.com/kuvera-apdl/apdl/commit/ab1815e3beb583e8c6aa25be6aa66bcfd2f66e68) |
| `PR125:H-15` | Most API services lacked a streaming raw request-body ceiling | New cross-service resource-exhaustion boundary, adjacent to but broader than `OSS-2026-07-22:H-09`/`H-10` | [`86607fe`](https://github.com/kuvera-apdl/apdl/commit/86607fe782f0d1b22e441891e5ef07d6d5d74549) |
| `PR125:H-16` | Query readiness ignored decision-critical schemas and Config capability | Follow-up deployment/readiness gate for the `OSS-2026-07-22:H-02` completeness subsystem | [`52cae89`](https://github.com/kuvera-apdl/apdl/commit/52cae893b53de6ffff0067fc70a484dfb93fc535) |
| `PR125:H-17` | Four official images were never run from published digests | Completes exact-artifact smoke evidence associated with `OSS-2026-07-22:H-15` and the audit's release/distribution gaps | [`d4c7402`](https://github.com/kuvera-apdl/apdl/commit/d4c7402ea041c33f261c4b9108df9a9f61a662ad) |
| `PR125:H-18` | Privileged GitHub Actions used mutable major tags | Direct implementation of the mutable-Actions release/distribution gap in the 2026-07-22 audit | [`d17fff9`](https://github.com/kuvera-apdl/apdl/commit/d17fff9ff13d4291707746af7c9fd06cfaa2ddf9) |

## Claim boundary

“All 18 High findings” in PR #125 refers only to
`PR125:H-01`–`PR125:H-18`. It does not renumber or automatically close
`OSS-2026-07-22:H-01`–`H-17`, the two Critical findings, or the earlier
`RA-01`–`RA-18` register.

In particular, the 2026-07-22 product-completeness finding
`OSS-2026-07-22:H-06` remains broader than the evidence-only experiment agent
added in PR #124; disabled feature-proposal and personalization workflows are
still outside that implementation. Earlier `RA-16` and `RA-17` also remain
partial for the reasons recorded in the
[findings register](reaudit-2026-07-16-findings-register.md). A release
decision must assess each namespace independently against its named snapshot
and exact cumulative head.
