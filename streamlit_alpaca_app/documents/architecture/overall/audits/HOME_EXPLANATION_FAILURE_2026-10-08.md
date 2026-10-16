# Home explanation failure — October 8, 2026

## Finding

Production Home publishes degraded research as a successful, available snapshot.
The OpenAI revenue report was already retained before Home started, but the
ticker-first research path did not supply it to the selected subjects. All twelve
subsequent ticker enrichments timed out. The market summary failed, group synthesis
was skipped, and generic descriptions of stocks moving together reached Home.

This diagnostic audit did not change application code, production configuration,
knowledge or deployments, or start a research conversation or job. Evidence came
from the public Home response, read-only production dataset/job/model records,
exact historical Blob versions and the existing source paths.

## Snapshot and timing

All local times below are Pacific daylight time on October 8.

| Observation | Time / result |
| --- | --- |
| OpenAI headline published | 09:45:03; 16:45:03 UTC |
| Raw news version available | 11:19:18; 18:19:18 UTC |
| Home snapshot started | 11:20:32; 18:20:32 UTC |
| Home job completed | 11:53:57; 18:53:57 UTC; `Succeeded` |
| Public Home state | `available`, seven beats, `summary: null` |

Home run: `584a033a-d455-4c43-8232-86e29f9dee42`.
Snapshot as-of: `2026-10-08T18:20:30.972175+00:00`.
The public API still served this run on the final audit read.

The selected daily-movers input was dated 09:35:28, before the report. Its next
version was ingested at 11:35:35, after Home loaded its inputs. Relevant Home
runs precede the intraday price producer by fifteen minutes. An 11:20 explanation
can therefore be selected from 09:35 moves while other UI prices refresh.

## Where the report was lost

The exact raw-news version available at Home start was
`news_articles__20261008T181916Z__741361d5`. It contained the
[Financial Times headline relayed by Benzinga](https://www.benzinga.com/news/26/10/62253618/openai-annualised-revenues-20bn-less-than-previously-signalled-financial-times)
about annualised revenue being $20bn below the earlier figure. Provider tags were
`MSFT`, `NVDA`, `ORCL` and `SPY`; the retained summary was empty.

Primary research selected `NIB, CMG, BNO, HUT, BSP, UGA, AAOI, MXL, PM, CLSK,
SBUX, AXTI`. None matched those four tags. Replaying the existing routing function
against the exact pre-run news frame delivered zero OpenAI articles to those
subjects. This was a local routing demonstration, not a new research run.

`pipeline/jobs/attention_home_build.py::_news_payloads_from_articles_frame`
only passes articles to subjects whose symbols occur in their provider tags.
`services/aql/collector.py::_candidate_context_documents` then reads that subject's
news payload. This path does not let a private-company event independently
nominate customers, suppliers, competitors or a shared theme. A useful indirect
lead is excluded before the LLM can assess the relationship.

The saved research produced 48 search requests and 105 results. None mentioned
OpenAI. Queries concentrated on company filings, offerings, earnings, trading
activity and individual-company news. Retaining a headline did not make it part
of explanatory research.

## Why the explanations are sparse

`attention_ticker_zopedia_enrichments__20261008T182030Z__584a033a` contains twelve
ticker rows with `status=failed` and `failure_categories_json=["timeout"]`:
`HUT, AAOI, MXL, CLSK, CIFR, TSEM, IREN, AXTI, CRWV, BTDR, COHR, VICR`.
Its thirteenth row, `__MARKET_SUMMARY__`, also failed and returned no summary.
The summary's precise failure stage was discarded; it cannot be inferred from
the ticker timeout labels.

The Home wrapper gives a whole agent attempt ninety seconds, despite allowing
multiple tools and model steps with separate budgets. `_call_with_timeout`
starts a daemon thread, waits and raises when its wait expires. It does not stop
the agent or return its collected evidence and draft.

The model ledger shows 54 successful research steps, 52 successful trajectory
monitor calls, 11 successful answer-judge calls and eight successful final
synthesis calls on the ticker-enrichment surface. The twelve agent model-call
spans were approximately 91–365 seconds, all exceeding the wrapper's wait. These
are lower bounds from first model request to last model return, not complete
agent runtimes. Research continued after its Home caller discarded the result.
Provider deadline failures also occurred; this was not a complete provider outage.

Group synthesis reported `skipped`, zero source items and zero changes.
`_group_synthesis_source_items` skips groups without member Zopedia or business
contexts. Missing member answers therefore prevented an independent group
investigation. Its normal synthesis path also has zero tool calls and cannot
discover a missing report itself.

Four of seven published beats merely described a cohort falling together.
`services/aql/assembler.py::build_observed_cohort_event_bundles` supplies generic
taxonomy-based prose. The final public editor allows these observed cohorts to
remain in top events. It reviewed sixteen items and changed nine, but did not
supply the missing causal research. Another AI-linked story attributed supplier
declines to Bitcoin weakness without discussing the retained OpenAI report.
The audit establishes a missed investigation, not that one report explains every
affected stock.

## Failure predates the harness release

| Enrichment version time (UTC) | Failed ticker rows | Market summary |
| --- | ---: | --- |
| October 7, 18:20 | 12 / 12 | Failed |
| October 7, 20:51 | 12 / 12 | Failed |
| October 8, 14:20 | 12 / 12 | Completed |
| October 8, 16:20 | 12 / 12 | Completed |
| October 8, 18:20 | 12 / 12 | Failed |

Both October 7 versions precede production promotion at approximately October 8,
04:49 UTC. The failure cannot be attributed solely to the language policy or new
harness release. Release checks verified service availability, settings, shared
harness contracts and controlled LLM answers, but did not inspect successful
scheduled Home research. A 200/available response did not establish useful
explanations. That was an acceptance gap.

## Repair at the shared owners

1. Let retained articles and shared events nominate research before final ticker
   selection. Use the shared AQL/Zopedia engine to infer affected companies and
   themes from current sources and business relationships. Provider tags remain
   useful metadata, never an exact-match admission requirement.
2. Give the shared engine one run budget covering preparation, tools, synthesis
   and review. Return collected sources and a useful draft before the caller
   deadline, retaining actual review state. Support cancellation or durable
   continuation so abandoned callers do not leave invisible work behind. Raising
   only the Home timeout would preserve the ownership defect.
3. Supply current event sources and observations to group research even when
   individual enrichment fails. Missing member answers are research gaps, not a
   reason to skip investigating a shared move. Keep this capability in the
   common engine rather than adding a Home-only researcher.
4. Separate research completion from publication health. Persist sanitized
   errors, run links, source coverage and useful partial results. Numerical
   availability can coexist with degraded research; infrastructure success must
   not conceal twelve failed explanations. Keep operational detail in Admin and
   telemetry without filling Home with implementation text.
5. Order Home after its current-price inputs, or trigger it from completed input
   versions. Preserve actual source times through publication.

Acceptance requires personally reading real scheduled Home output and its source
trail across several unrelated shared-event and sparse-news examples. The output
must explain what changed, why affected businesses matter and which connection
remains a hypothesis. Inspect scheduled completion as well as endpoint health.
Do not encode today's companies or headline as rules or an inclusion test.

Compact retained evidence: [audit receipt](HOME_EXPLANATION_FAILURE_2026-10-08.json).
