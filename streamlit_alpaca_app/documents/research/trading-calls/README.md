# Trading calls and approval tracking

Owner request: improve weak Trading reasoning, require a preferred direction despite uncertainty, and track calls after approval. [Implementation plan](../../plans/TRADING_CALLS_AND_APPROVAL_TRACKING_2026-10-16.md).

**Current state: unaccepted reasoning candidate; no release.** The paper approval/price path is integrated locally and its objective contracts are verified. The same cheap-model reasoning still has material economic errors, including when research is focused on one company. A direction enum and a completed review do not establish a useful forecast.

**Owner's clarified bar:** the framework needs to support generalized, recoverable, measurable iteration; current predictions need not be perfect. The foundation is suitable for a paper pilot after normal integration checks. The failed reasoning experiments remain valid evidence. Approved-call outcomes still need connecting to the existing review/learning workflow before claiming automatic improvement. [Revised acceptance](../../plans/TRADING_CALLS_AND_APPROVAL_TRACKING_2026-10-16.md#acceptance-record).

- [October 18 comparison and personal assessment](experiments/2026-10-18/ASSESSMENT.md): current-source direct/reviewed comparisons, all five horizons and two focused company-research probes. Full outputs and provider receipts are retained beside the assessment.
- [October 18 preservation and software verification](../../verification/trading-calls-2026-10-18/README.md): scoped integration against the deployed hotfix baseline; no unrelated runtime inventory files changed.
- [October 16 retained evidence](evidence/2026-10-16/manifest.json): frozen original inputs and complete historical experiments, including rejected numerical-scenario candidates. Historical implementation/source identities are evidence, not a release base.

An off-runtime company-owned forecast prototype was also tried with default Flash, Flash max and open-source Pro high. Material errors survived; no model switch or runtime rewrite is accepted. The next investigation needs original dated disclosures and transaction terms, rather than another rewrite of inherited model reports, with usable numerical tools. Preserve the cheap-open-model and whole-workload limits. Do not add ticker rules or another reviewer to compensate for these examples.
