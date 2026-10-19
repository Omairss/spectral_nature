# October 18 Trading comparison

## Decision

**Not accepted.** Explicit direction works, and the tracker contracts pass, but the economic reasoning remains unreliable. No build or deployment follows this experiment. This is a personal reading of complete outputs, not an automated prose grade or a forecast-accuracy result.

## Protocol and identities

`replay.py` imports the current Git checkout and uses the actual development worker's configured `deepseek-reasoner` client through the shared engine. The private worker configuration stays outside the repository. Inference uses the retained October 16 production/held-out company reports and immutable quote rows, with no current web access or knowledge writes. `input_sha256` identifies each context/report bundle. Comparisons keep the same client, inputs and shared engine `a087d63a3a2f168257d0`; Trading source hashes distinguish contracts.

| Arm | Horizons | Meaning |
| --- | --- | --- |
| `baseline-*` | 3m, 1w | Current accepted old contract. Six cards; two 3m cards remain watch/no-call. |
| `direct-*`, `reviewed-*` | 3m, 1w | Accidental old-contract repeat/review controls after source preparation failed. Retained and charged, not new-contract results. |
| `calls-direct-*`, `calls-reviewed-*` | 3m, 1w | Initial directional/price/assumptions contract; 16 directional cards in four outputs. |
| `economics-*` | 3m, 1w, 5y | Stronger common-share economics and conditional-pricing instructions; 12 cards. |
| `economics-heldout-*` | 1m, 1y | Same candidate on unfamiliar retained company reports; eight cards. |
| `source-asst.json`, `source-crgy.json` | Requested company horizons | Focused source-research probes using prior model research as evidence to reassess, not primary-source verification. |
| `packet-*` | ASST 3m; CRGY 1w/1m/3m | Company-owned economic basis and typed horizon calls, default Flash effort. |
| `packet-max-*`, `packet-pro-*` | Identical to packet controls | Separate effort/model probes: Flash max and open-source DeepSeek V4 Pro high. No runtime settings changed. |

I read the six baseline cards, all 36 initial new-contract cards, both complete source-research answers and all 12 company-packet calls. Old-contract accidental controls are retained as execution/cost records; they are not a new quality benchmark. Frozen source dates and the model's possible knowledge of later events limit historical claims. The source probes also inherit the current engine execution clock and are diagnostic probes, not a dated historical forecast comparison.

The two source probes ran after scoped approval integration changed the engine fingerprint to `fcf2c33ced6965b8474c`. Only the two approval functions changed in the fingerprinted store; the research/review implementation did not. Do not describe their engine as the unchanged primary-pair engine.

## Material findings

1. **Direction and confidence are separate.** New cards consistently choose a direction while retaining low confidence and opposing cases. The old contract genuinely converted uncertain completed research into watch/no-call; this was not proof that the provider refused probabilistic questions.
2. **Review is not an established repair.** The 3m new-contract review reports a deadline failure and preserves its valid draft, as the hotfix requires. The completed 1w reviewer introduced a 10% JetBlue invalidation without an economic basis and described two longs/two shorts when the result has one long/three shorts. Source mechanics completed; reasoning quality did not follow. Current Trading synthesis does not enable the experimental extra review.
3. **Common-share claims remain confused.** ASST outputs repeatedly compare gross bitcoin per common share with the stock price without consistently deducting senior claims. An annual 15% preferred cost is also used as a short-window return hurdle. The focused source report correctly computes $75m annual/$18.8m quarterly SATS cost and states the residual-value identity, but its final direction/invalidation still falls back to gross bitcoin-per-share growth. The correct formula in one paragraph did not repair the conclusion.
4. **Prices are still used to invent expectations.** The focused CRGY report says buyers at the $12.50 offering price pay nothing for $140m of savings and that the market assumes a neutral deal. No valuation establishes that. It also compares acquired-only cash flow per enlarged share count with pre-deal company cash flow per share, omitting the retained company's contribution from the combined numerator. Other outputs improve the conditional language but still treat financing or missing terms as sufficient proof of price direction.
5. **Some findings are useful and should survive correction.** CORZ distinguishes investment spending from operating losses; MRNA uses the seasonal same-quarter revenue base; near-term cards can keep an earnings event outside the weekly trigger window; the held-out CRGY annual case explicitly considers cash received, acquired operations and financing. These successes do not erase the material errors above.
6. **Absolute direction needs explicit ownership.** Several hypotheses say only “underperform,” although the ledger measures an absolute long/short return and separately compares SPY. The current source instruction now defines absolute higher/lower price. The packet probes include that instruction, but no matched all-horizon synthesis comparison of the final runtime revision has been accepted.
7. **A typed company forecast did not establish a repair either.** The off-runtime packet prototype makes one economic basis own the horizon calls and avoids a later rewrite. Default Flash still treats gross bitcoin growth as common-value growth and infers that an offering price gives no value to acquisition savings. Flash max improves the ASST residual-value definition and quarterly carry units, but CRGY still conflates total liabilities with debt, compares incompatible per-share/rate quantities, and treats absent proof as a reason for downside. Pro also repeats the gross-bitcoin mechanism in ASST and says undisclosed CRGY accretion is therefore unpriced. These are material reasoning issues, not failures to choose a direction. The prototype is not integrated into runtime.

## Separate effort/model check

The provider receipts report **`deepseek-flash`**, despite the configured `deepseek-reasoner` request name. Thinking is enabled; existing runs already spend thousands of reasoning tokens. The [configuration receipt](effective-configuration.json) confirms the current Trading system prompt has no active override. Do not describe this application baseline as Pro or as reasoning disabled.

The packet input hashes match across default Flash, Flash max and Pro high for each company. Actual reported models and effort controls are retained in [model-effort-receipts.json](model-effort-receipts.json). The publisher documents [thinking and effort mappings](https://api-docs.deepseek.com/guides/thinking_mode/), [available models/prices](https://api-docs.deepseek.com/quick_start/pricing/) and the [Pro MIT license](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/LICENSE). Default Flash completes the two packets in 39.2/37.0 seconds; Flash max takes 103.3/71.2 seconds; Pro high takes 105.4/100.0 seconds. This small exposed comparison does not establish general model rankings or a permissible whole-workload cost change.

## Costs and preparation errors

All **27** paid attempts, including accidental controls, six packet/effort/model calls and the review whose result missed its deadline, remain in `provider-attempts.json`; `cost-summary.json` gives a known upper estimate of **$0.543036516**, with no unpriced attempts in this October 18 run. Provider success can coexist with an unusable late review, so the provider ledger and engine result are both required. October 16 costs and its two unpriced attempts remain separate in the retained evidence. This run does not establish the +20% whole-workload limit.

The first source-edit helper handled `Assign` but not the schema's `AnnAssign`; it failed before writing source. The shell nevertheless started old-contract comparisons. Corrected preparation and kept those controls rather than relabelling them. Two later source probes rejected unsupported run IDs before inference; the final harness uses the shared engine's own ID generation.

The original source-probe harness captured file identity after inference. ASST records loaded source `2b177064…`; CRGY records later disk source `a425a704…`, after an instruction outside `_aql_ticker_query` changed during execution. Both probes used the process-loaded `2b177064…` Trading module and the same ticker-query definition. The complete original result/query is retained; `provenance-audit.json` records this limit without rewriting the output. The harness now captures identity before execution. No paid rerun was made to conceal the metadata mistake.

## Next approach

Do not add more example-specific instructions, a mandatory reviewer or an automatic model upgrade. The company-owned packet was tried locally and does not yet justify a runtime rewrite. The next investigation must expose original dated financial disclosures and transaction terms to the same analysis owner, rather than treating prior model reports as the factual boundary. It must distinguish source observations, transaction economics and conditional price judgment, with effective numerical tools available where calculation is needed. The current retained-report probes cannot establish that source acquisition path. Keep the cheap-open-model constraint and compare unfamiliar cases; measure complete-workload cost only after the reasoning is useful. Normal authenticated dev/UI checks and release-source acceptance follow local quality acceptance.
