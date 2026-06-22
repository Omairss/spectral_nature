# Claude Instructions
Refer philosophy.md. Update this file if it seems stale or conflicts with a user request.

The job vs UI distinction is key. UI should only render pre-loaded content whenever possible. The jobs should be doing the compute and db heavylifting.

Don't add random filler text in the UI. Ask and remove these filler texts as you come across it.

Log and update mistakes and learnings to learnings.md and mistakes.md. Maintain these files - cleaup, compact, summarize each file as needed. Also maintain a depricated.md to keep track of stale paths.

Read, think, check relevant documentation before implementing anything.
Prefer fixing things at source. If your solution seems surface level, reevaluate if it really is a surface level task or a deep task.

When tasked with NLP features, DO NOT HARDCODE. Use LLMs when possible. No fixed software engineering style tests. You will qualitatively analyze results and check if it's up to the bar of excellence and generalizable.
NO hardcoding solutions.
NO one off fixes, surface level bandaid changes. Don't be afraid to propose big rewrites when neeeded.
ROBUST DESIGN. NOT fragile design with a ton of bandaids.
**DO NOT** use convoluted language like 'slot verdict', 'searched evidence' etc.
**DO NOT** Overfixate on deterministic code.
**DO NOT** attempt to create a durable artifact quickly.
**DO NOT** write helper code and bandaid fixes. No hedging.
**DO NOT** prioritize getting a working artifact over the quality.
**DO NOT** patched quality failures locally. Deep fix.
**DO NOT** Try to close the task superficially. If it doesn't work. You can return and tell me the state and we will try another approach.
**DO** Understand the spirit of the question and implement it deeply. **NO LAZINESS**
Forget about test and forget about passing tests for anything based on LLM qualitative assesments. YOU will personally evaluate how good this is. DO NOT treat LLM development as traditional software engineering

Prefer using simple language when possible.
Write down major changes, design architectures, plans often. Update it often so you can refer to it across sessions. This should be under documents/. Create appropriate folders if neccesary.
Auto deploy changes to dev, but not to prod without explicit permission.
Evaluate reliability/complexity. When something can't be done reliably, requires hacky/unreliable techniques seems overly complicated for the task at hand, double check with me.
