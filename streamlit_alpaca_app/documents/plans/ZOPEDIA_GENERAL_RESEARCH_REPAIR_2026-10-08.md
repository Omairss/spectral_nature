# Shared Zopedia research repair — October 8, 2026

Status: shared implementation and development rollout verified.
Production applications have not been deployed for this repair. Shared retained-store
date corrections and additive indexing infrastructure are recorded below.

The two audited chat failures are examples of shared retrieval and research failures.
No company, sector, headline, required conclusion or wording rule will be added for them.
The system continues to maximize useful recall and support reasonable inference.

## Design

1. Separate embedding transport configuration from the chat model. Populate a
   corpus-wide PostgreSQL vector index beside lexical retrieval. New original
   news enters retained storage at ingestion; indexing runs outside materialization.
   Search combines varied model-selected queries and candidate ranking. Generated
   answers retain their type and structured original-source dependencies.
2. Carry the original question, conversation, visible references, as-of time,
   timezone, source permissions and write policy explicitly. The planner updates
   a compact research state describing findings, alternatives and useful next work.
3. Bound research by actual acquisition count and elapsed time. Planning repairs
   do not consume acquisition opportunities. Reflection belongs in planning;
   additional monitoring is requested when useful. Independent searches can run
   together. Checkpoint collected sources, state, pending actions and usable drafts.
4. Synthesis gives the best useful assessment. Sources inform facts and reasoning,
   without proof-of-causality or exact-citation gates. Review improves the answer
   and preserves a valid draft if review fails.
5. Use the active global language configuration, confirmed as the 80%
   ASD-STE100-inspired plain-English preference. Preserve paragraphs, Markdown
   and citations from generation through persistence and product DTOs.

## Evaluation and release

Personally read live shared-engine outputs for the original questions and unseen
questions covering business context, concepts, time-sensitive research, historical
comparison, numerical work and follow-ups. Include retained-only/no-write scope
and misleading prior analysis. Inspect source discovery and output usefulness;
do not encode expected stories or keywords as tests. Check mechanical contracts
separately. Release an isolated development image only after local results satisfy
the request. Record actual results, index coverage, limitations and release receipt.

## Findings during implementation

- Active development writing policy is the configured global default; no new style
  needs inventing. Existing transport injects that policy into all model requests.
- All 11,340 retained chunks have no embeddings. Existing embedding loader sends
  requests to the DeepSeek chat endpoint. It cannot populate semantic retrieval.
- PostgreSQL offers pgvector 0.8.2 but it is not enabled. The existing Azure OpenAI
  account has chat only, so embedding availability must be established first.
- Keep unrelated workspace changes out of the development release using a saved
  working baseline and source-guarded overlays.

## Implementation findings and corrections

- Embeddings now use a dedicated Azure deployment and key. The first complete
  pass covered 13,393 retained chunks, including current wiki projections and
  original news snapshots. No generation model was changed.
- Missing pandas dates were adapted by psycopg into year 48,113. Corrected
  3,779 chunk dates and 2,244 document dates to NULL, preserving the original
  invalid value in metadata. Both persistence boundaries now convert NaT to NULL.
  A real psycopg write into a temporary table verified the correction.
- Home snapshot search now delegates to the shared corpus search. Candidate
  acquisition diversifies document identities and source origins before limits.
  Company hints supplement broad recall; an explicit strict scope remains available.
- Dated searches also expose broad reporting from the requested period. This
  avoids requiring the model to guess a development's vocabulary before discovery.
  Known publication dates and unknown dates remain distinct acquisition paths.
- Removed unconditional prefetch and redundant source-ranking model calls after
  live runs showed anchoring and repeated ranking timeouts. The planner now reads
  one combined, deduplicated source catalog and selects useful sources itself.
  Full source reads remain separately retained beside compact candidate excerpts.
- Replaced verbose typed-argument envelopes with a JSON argument object encoded
  as a string, validated against the selected tool's complete input schema.
- The editor no longer has mandatory unsupported-claim and evidence-gap lists.
  It corrects errors, checks numerical meaning, improves clarity and can request
  useful additional research. Useful inference does not require certification.
- Checkpoints preserve pending work, full collected sources and drafts privately.
  A durable database round trip preserved paragraphs and typed source dependencies;
  the public event callback omitted the private checkpoint.
- Search now interprets the complete request and application time context through
  the shared model gateway. A separate retrieval plan supplies varied queries and
  a reporting window. Optional caller dates are no longer the only way a current
  question gets broad dated reporting. Calendar-day boundaries use the user's
  timezone. Independent query failures preserve successful acquisitions.
- Research and writing now have different inputs. The researcher keeps broad
  findings, selected sources and a short outline for the reader. Review happens
  while useful follow-up can still execute and returns corrected outline points.
  The final writer receives those points and a deduplicated selected citation index, without a second
  editor replacing its prose afterward. It must preserve the analysis rather than
  introduce new conclusions while simplifying language.
- Removed unused keyword-based market-recovery and evidence-count helpers.
  Follow-up text stays literal; separate conversation and checkpoints provide
  context. Short replies now pass the API, repository and browser boundaries.
- Knowledge ingestion tools now declare their write effect. No-write runs cannot
  discover ingestion or mutation tools, and tool execution honors the allowed
  registry. The model can narrow application write permissions when the user's
  request forbids knowledge changes. No-write audit logging does not feed generated
  answers back into searchable memory.
- A real database contract exercise verified context-aware idempotency, parent
  ownership, short follow-ups, private checkpoints, paragraph/citation preservation,
  no-write retention and the explicit eight-tool/300-second queued budget.

## Local evaluation and development rollout

Live shared-engine runs now recover the retained OpenAI report naturally for both
the broad market question and the AI-stock question. The broad question ranks the
oil shock first and explains its timing, then distinguishes the AI revenue report.
The AI question connects revenue doubts, funding, borrowing costs and broader selling.
No required article, company query, cause order or expected answer was encoded.

The retained-only Ciena run explains optical equipment demand, distinguishes company
results from dated deployments and analyst estimates, and cites retained titles.
Concept and zero-acquisition runs explain profit versus cash with coherent numbers.
A resumed two-sentence follow-up uses the earlier research without spending another
acquisition budget on carried sources. Paragraphs and useful source links survive.

These checks support a development release, not a guarantee for every question.
The broader gas/electricity sample still exposed an overly loose inference about
alternative power technologies and some unexplained abbreviations. Answers can also
include more secondary figures than necessary. Those are retained qualitative
limitations; no topic rule, proof gate or phrase filter was added to hide them.
Earlier failed evaluations remain separate from accepted retrieval and flow checks.

Development images use guarded task overlays, preserving unrelated workspace work.
An undeclared merge-conflict file in the first worker payload caused a pre-claim
startup failure; the prior worker image was restored. The replacement verifies the
complete payload inventory and imports the actual runtime entrypoint. It completed
the live queued question and its follow-up. Both retained private checkpoints,
preserved public paragraphs/citations and wrote no generated chunks to searchable
knowledge. The index job completed with 13,443 of 13,443 chunks covered. API, web and
eight dev workers were updated; production application configuration was verified
unchanged. See the [development release receipt](../architecture/AQL/audits/DEV_ZOPEDIA_GENERAL_REPAIR_2026-10-08.json).

The deployed long answer still includes unnecessary causal caveats and secondary
figures. The follow-up is much clearer but overgeneralizes one market effect.
The writing preference is active, and quality has improved; these observations
remain open quality work. The browser extension could not attach, so authenticated
UI rendering was not visually verified. HTTP checks, deployed endpoint functions,
real managed-identity dispatch and worker execution were verified separately.

Checkpoint state supports continuation and survives recoverable model failures.
Automatic re-dispatch after a worker dies is not implemented; do not describe this
release as complete automatic worker recovery. The generation model remains selected
by the shared configuration. No generation provider or model migration was made.

Embedding protocol reference: [official OpenAI API documentation](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
