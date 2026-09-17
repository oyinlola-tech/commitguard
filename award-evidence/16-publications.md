# 16 - Publications and written output

No peer-reviewed publication exists. What exists is a documented technical record,
written to be read by engineers who were not involved.

| Output | State | Where |
|---|---|---|
| Research documentation: question, methodology, six evaluations, fuzzing, reproducibility, limitations, innovation, impact | **IMPLEMENTED** - 18 documents | [docs/research/](../docs/research/) |
| Architecture paper | **IMPLEMENTED** | [docs/research/commitguard-architecture.md](../docs/research/commitguard-architecture.md) |
| Architecture decision records | **IMPLEMENTED** - 8 records with costs stated | [docs/adr/](../docs/adr/) |
| Threat model with per-threat test evidence | **IMPLEMENTED** | [docs/security/threat-model.md](../docs/security/threat-model.md) |
| Security documentation: boundaries, authorization, review guide, incident and vulnerability response, supply chain, CI review | **IMPLEMENTED** - 9 documents | [docs/security/](../docs/security/) |
| API and CLI references | **IMPLEMENTED** | [docs/api/](../docs/api/), [docs/cli/](../docs/cli/) |
| Deployment and operations | **IMPLEMENTED** | [docs/deployment/](../docs/deployment/), [docs/operations/runbook.md](../docs/operations/runbook.md) |
| Generated security and benchmark reports | **IMPLEMENTED** - rebuilt from evidence, never hand-edited | `reports/` |
| Technical article: *Building Defence in Depth for Git Contribution Security* | **IMPLEMENTED** | [docs/presentations/technical-article.md](../docs/presentations/technical-article.md) |
| Technical presentation outline | **IMPLEMENTED** | [docs/presentations/commitguard-technical-overview.md](../docs/presentations/commitguard-technical-overview.md) |
| Citation metadata | **IMPLEMENTED** | `CITATION.cff` |
| Conference or journal submission | **PLANNED** | - |

## Writing standard used throughout

Every document states what is measured, what is tested and what is not; labels
claims; names the test or result file behind each assertion; and keeps a
limitations section. The reports are generated from recorded evidence, so a
documentation claim cannot drift from the measurement it cites - and several tests
fail if documentation contradicts the code.
