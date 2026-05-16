# Social Interaction Layer Plan

Date: 2026-05-05

## Goal

Add a social interaction layer around the project without putting new research, LLM, or external API work on the page-render path.

The best first surfaces are:

- Home summaries and Home event/mover cards
- Stock Investigator agentic summaries
- Trading Agent candidates, after each run/candidate has a stable persisted identity

## Existing Context

The app already has the right foundation:

- `streamlit_alpaca_app/app.py` renders Home, Stock Investigator, and Trading Agent.
- Home uses materialized attention payloads through `_load_attention_home_1d_cached`.
- Stock Investigator renders `_render_page_agentic_summary_panel`, which reads `page_agentic_summaries`.
- Trading Agent is admin-only and explicit. It runs after `Run Trading Agent` and consumes materialized market opportunity and page-summary context.
- `services/auth_store.py` already owns app user state in Postgres under `APP_ACCESS_SCHEMA`.
- `auth_store` already creates `content_posts` and `content_comments`, but there is no implemented product path for them yet.
- Access telemetry already records user, section, and click events through `record_access_event`.
- The data-access layer exposes materialized content like `attention_home_1d`, `page_agentic_summary`, and `market_opportunity_feed`.

Relevant local guardrails:

- `documents/reference/AGENTIC_MARKET_TASK_GUARDRAILS.md`
- `documents/reference/ATTENTION_FEED_GUIDELINES.md`
- `documents/learnings.md`: especially 3, 16, 31, 49, 50, 51, 53, 54
- `documents/mistakes.md`: especially 31, 32, 37, 39, 40, 45, 46

## Documentation Checked

- Streamlit `st.feedback`: supports thumbs, faces, and stars. Return values are integer selections, with thumbs returning `0` for down and `1` for up. This is good for lightweight reactions, not threaded comments.
  - https://docs.streamlit.io/develop/api-reference/widgets/st.feedback
- Streamlit static file serving: requires `enableStaticServing = true` or `STREAMLIT_SERVER_ENABLE_STATIC_SERVING=true`; current `.streamlit/config.toml` does not enable this.
  - https://docs.streamlit.io/develop/concepts/configuration/serving-static-files
- FastAPI static files: the existing API container can mount a separate static app under a path. This is a better fit for public share pages and OG images than trying to make Streamlit serve dynamic share metadata.
  - https://fastapi.tiangolo.com/tutorial/static-files/
- Web Share API: `navigator.share()` needs HTTPS, user activation, and browser support is not universal. Use it as progressive enhancement with copy-link fallback.
  - https://developer.mozilla.org/en-US/docs/Web/API/Navigator/share
- Open Graph protocol: share pages need `og:title`, `og:type`, `og:image`, and `og:url`, with `og:description` strongly useful.
  - https://ogp.me/
- Stocktwits official developer page currently says new registrations are not being accepted while APIs, docs, and terms are reviewed. Treat direct Stocktwits ingestion/posting as unavailable unless existing approved credentials exist.
  - https://api.stocktwits.com/developers
- Reddit official API includes `/api/submit`, but posting requires OAuth scope and subreddit-specific requirements. This is not a good first slice for product interaction.
  - https://www.reddit.com/dev/api/
- X API docs currently emphasize paid/enterprise API access. Prefer user-initiated share links over automated posting for the first release.
  - https://docs.x.com/overview

## Recommended Product Shape

Build two layers:

1. Internal social interaction:
   - reactions
   - comments
   - follow-up requests
   - "watch this" or "skeptical" signals
   - admin moderation/status

2. External sharing:
   - copyable share text
   - authenticated or public share page
   - native device share when supported
   - outbound share intent links

Do not start with automated posting to X, Reddit, LinkedIn, or Stocktwits. It adds OAuth, moderation, platform policy, and support complexity before the product value is proven.

## Content Anchor Contract

Every social action must attach to a stable content anchor, not rendered markdown.

Use this shape:

```json
{
  "surface": "home_summary",
  "object_type": "summary",
  "object_id": "attention_home_1d:<run_id_or_hash>",
  "source_dataset": "attention_home_1d",
  "run_id": "...",
  "asof_time_utc": "...",
  "ticker": "",
  "bundle_id": "",
  "context_signature": "",
  "object_digest": "...",
  "visibility": "portfolio"
}
```

Surface-specific anchors:

- Home summary: `attention_home_1d:{run_id}` when `run_id` exists, else digest of `generated_at_utc + homepage_summary.input_hash + summary_text`.
- Home event: `attention_event:{run_id}:{event_id}`.
- Home mover: `attention_mover:{run_id}:{symbol}:{object_digest}`.
- Stock summary: `page_agentic_summary:{surface}:{ticker}:{context_signature}`.
- Trading Agent candidate: `trading_agent_candidate:{stored_run_id}:{ticker}:{candidate_digest}`.

Trading Agent currently stores its result in `st.session_state`. Before adding comments or reactions there, persist each run and candidate to a store so anchors survive reruns, refreshes, and API/mobile clients.

## Data Model

Use the existing app auth Postgres schema because this is user-owned product state, not pipeline evidence.

Extend existing content tables instead of creating a parallel social schema:

### `content_posts`

Use one row as the discussion root for an anchored object.

Add columns:

- `source_surface TEXT NULL`
- `source_object_type TEXT NULL`
- `source_object_id TEXT NULL`
- `source_dataset TEXT NULL`
- `ticker TEXT NULL`
- `run_id TEXT NULL`
- `context_signature TEXT NULL`
- `object_digest TEXT NULL`
- `metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb`

Add indexes:

- unique active anchor: `(portfolio_id, source_surface, source_object_type, source_object_id)` where `status = 'active'`
- `(source_surface, ticker, created_at DESC)`
- `(source_object_id)`

### `content_comments`

Keep the existing table. Add only if needed:

- `edited_at TIMESTAMPTZ NULL`
- `metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb`

### `content_reactions`

New table:

- `id UUID PRIMARY KEY`
- `post_id UUID NOT NULL REFERENCES content_posts(id) ON DELETE CASCADE`
- `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- `reaction_type TEXT NOT NULL`
- `value TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

Unique index:

- `(post_id, user_id, reaction_type)`

Recommended reaction types:

- `useful`
- `skeptical`
- `watch`
- `follow_up`
- `publish_ready` for admin review flows

### `content_share_artifacts`

New table for public or authenticated share pages:

- `id UUID PRIMARY KEY`
- `post_id UUID NOT NULL REFERENCES content_posts(id) ON DELETE CASCADE`
- `slug TEXT NOT NULL UNIQUE`
- `visibility TEXT NOT NULL`
- `title TEXT NOT NULL`
- `description TEXT NOT NULL`
- `body_markdown TEXT NOT NULL`
- `image_url TEXT NULL`
- `created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- `created_at TIMESTAMPTZ NOT NULL`
- `expires_at TIMESTAMPTZ NULL`
- `status TEXT NOT NULL`
- `metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb`

Visibility values:

- `portfolio`: requires login and same portfolio membership
- `private_link`: unlisted but accessible by signed token
- `public`: safe public content only

Default to `portfolio`. Only allow `public` for sanitized Home/Stock summary cards, never portfolio/account values or Trading Agent candidates without explicit admin action.

## Service Boundary

Add `services/social_interactions.py`.

This service owns:

- building anchors
- upserting discussion roots
- listing reactions/comments
- toggling reactions
- creating comments
- moderating comments
- building share artifacts
- returning compact counts for UI cards

Public functions:

```python
def build_home_summary_anchor(home_payload: dict[str, object]) -> dict[str, object]: ...
def build_home_event_anchor(event: dict[str, object], *, run_id: str = "") -> dict[str, object]: ...
def build_stock_summary_anchor(summary: dict[str, object], *, ticker: str, context_signature: str) -> dict[str, object]: ...
def build_trading_candidate_anchor(run: dict[str, object], candidate: dict[str, object]) -> dict[str, object]: ...

def get_or_create_thread(anchor: dict[str, object], *, user: UserContext) -> dict[str, object]: ...
def load_thread(anchor: dict[str, object], *, user: UserContext) -> dict[str, object]: ...
def toggle_reaction(thread_id: str, reaction_type: str, value: str, *, user: UserContext) -> dict[str, object]: ...
def add_comment(thread_id: str, body: str, *, user: UserContext, parent_comment_id: str = "") -> dict[str, object]: ...
def list_activity(surface: str = "", ticker: str = "", limit: int = 20, *, user: UserContext) -> list[dict[str, object]]: ...
def create_share_artifact(thread_id: str, *, user: UserContext, visibility: str) -> dict[str, object]: ...
```

Keep raw SQL inside `auth_store.py` or a small store module that uses the same schema helper. Do not put SQL in `app.py`.

## Streamlit UI Plan

Add one reusable renderer:

```python
def _render_social_strip(anchor: dict[str, object], *, key_prefix: str, compact: bool = True) -> None:
    ...
```

Behavior:

- If no database auth user exists, show only share/copy actions for public-safe content.
- If a user exists, show reaction buttons and a comment expander.
- Use compact counts on cards.
- Load full comments only when the expander is opened.
- Never run AQL or LLM from this component.

### Home Summary

Location: `_render_attention_home_summary_card`.

Add:

- reactions under the summary body
- "Discuss" expander
- "Share" action that creates/copies a sanitized summary share artifact

Anchor:

- `build_home_summary_anchor(home_payload)`

### Home Event And Mover Cards

Locations:

- `_render_attention_home_event_card`
- `_render_attention_home_mover_card`

Add:

- compact reaction counts
- watch/follow-up action
- optional share link for public-safe event cards

Avoid opening a comment thread for every card by default; show a compact button and lazy-load the thread when clicked.

### Stock Investigator

Location: after `_render_page_agentic_summary_panel` inside `_render_stock_investigator_workspace`.

Add:

- ticker discussion thread anchored to the resolved materialized summary
- reaction types: useful, skeptical, watch, follow_up
- "Share ticker read" using sanitized headline/summary/watch items

Anchor:

- `build_stock_summary_anchor(summary, ticker=ticker, context_signature=context_signature)`

### Trading Agent

Before social UI:

- persist Trading Agent runs and candidates with stable IDs
- store the exact input context hash, AQL run ID, output candidates, and generated-at time
- expose latest run history for admins

Then add:

- "Publish for review" on each candidate
- internal discussion thread only after publish
- reactions: watch, skeptical, follow_up, publish_ready

Default visibility:

- `portfolio` or `admin_only`

Do not expose public share for Trading Agent candidates in the first release. The wording is close to market recommendations, so keep it internal and reviewable.

## API Plan

Streamlit can call the service directly. The FastAPI app should expose the same capability for iOS and external clients.

Add endpoints:

- `GET /v1/social/thread?surface=...&object_id=...`
- `POST /v1/social/thread`
- `POST /v1/social/reaction`
- `POST /v1/social/comment`
- `GET /v1/social/activity`
- `POST /v1/social/share-artifact`
- `GET /share/{slug}` for share HTML with Open Graph tags

Auth:

- read portfolio/private threads: authenticated user with matching portfolio
- write reactions/comments: authenticated user
- moderate: admin
- public share page: only exposes sanitized share artifact body, not raw payloads

Add API scopes later only if agent/API keys need social access:

- `social:read`
- `social:write`
- `social:moderate`

## External Sharing Plan

Phase 1:

- Build share text from structured payloads.
- Provide copy-to-clipboard fallback.
- Provide native Web Share API through `components_html` only when HTTPS/user gesture is available.
- Provide outbound links for user-initiated posting where safe.

Phase 2:

- Public share pages under FastAPI `/share/{slug}` with OG metadata.
- Optional static/card image generation.
- Store share artifact metadata in Postgres.

Phase 3:

- Direct posting through platform APIs only after credentials, OAuth, moderation, and platform terms are confirmed.

Avoid direct Stocktwits API work unless approved credentials already exist. Avoid Reddit submit automation until subreddit rules, OAuth, and moderation flow are clear.

## External Social Signal Ingestion

This is a separate feature from user interaction.

Do not scrape social media pages.

If social sentiment is needed:

- create a `social_mentions` materialized dataset
- ingest only from official APIs or licensed vendors
- run ingestion in scheduled jobs, not Streamlit render
- summarize via AQL/SAA evidence paths
- surface only aggregate signal and representative public links

Candidate dataset:

- `social_mentions`
- `social_symbol_sentiment_daily`
- `social_trending_tickers`

Potential providers:

- Reddit official API, if OAuth and policy fit
- X API, if paid access is accepted
- Stocktwits Firestream or approved API credentials, if available
- a licensed market social-data vendor

## Implementation Phases

### Phase 0 - Design And Tests First

Files:

- `documents/plans/SOCIAL_INTERACTION_LAYER_PLAN_2026-05-05.md`
- `tests/test_social_interactions.py`

Tasks:

- add anchor-building tests for Home, Stock, and Trading Agent
- add permission tests for portfolio-only visibility
- add reaction toggle contract tests
- add comment sanitization tests

### Phase 1 - Store And Service

Files:

- `services/social_interactions.py`
- `services/auth_store.py`
- `tests/test_social_interactions.py`

Tasks:

- extend schema using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- add `content_reactions` and `content_share_artifacts`
- implement thread upsert, reaction toggle, comment create/list
- return compact counts without loading all comment bodies
- record high-signal social events through `record_access_event`

Verification:

- unit tests with mocked store connection
- direct store tests for SQL where practical

### Phase 2 - Home And Stock UI

Files:

- `app.py`
- `presentation/attention_content.py` only if helpers belong outside app
- tests using `streamlit.testing.v1.AppTest` if feasible

Tasks:

- add `_render_social_strip`
- wire Home summary social strip
- wire Stock Investigator summary social strip
- add lazy comment expander
- add sanitized share text generation

Verification:

- render check for Home summary social controls
- render check for Stock Investigator summary social controls
- verify no AQL/LLM calls happen from the social component

### Phase 3 - Trading Agent Persistence And Review

Files:

- `services/trading_agent.py`
- `services/trading_agent_store.py` or `services/social_interactions.py`
- `app.py`
- `tests/test_trading_agent.py`
- `tests/test_social_interactions.py`

Tasks:

- persist Trading Agent run result after successful generation
- persist candidate rows with stable candidate digests
- add run history in admin view
- add "Publish for review" action on candidate cards
- add social thread only for published candidates

Verification:

- candidate IDs are stable for same run payload
- rerun does not duplicate the same persisted candidate
- unpublished candidates are not visible outside admin

### Phase 4 - API And Mobile Read Path

Files:

- `api/main.py`
- `services/api_auth.py` if new scopes are needed
- `tests/test_api_v1.py`

Tasks:

- add read/write social endpoints
- enforce auth and portfolio visibility
- add share artifact endpoint
- expose social counts in dataset or endpoint responses for mobile clients

### Phase 5 - Public Share Pages

Files:

- `api/main.py`
- `services/share_cards.py` or `services/social_interactions.py`
- static asset directory if needed

Tasks:

- serve `/share/{slug}` from FastAPI
- render simple HTML with OG tags
- include canonical URL, title, description, and image
- use no private/account data
- support expired/private share pages cleanly

Verification:

- inspect generated HTML meta tags
- test private/public access behavior
- test share artifact has no portfolio/account fields

### Phase 6 - Optional External Social Ingestion

Only start this after the internal layer proves useful.

Tasks:

- pick one official/licensed source
- create materialized datasets
- add ingestion job
- add AQL summarization/evidence integration
- expose aggregate signal on Home and Stock pages

Reliability checkpoint:

- if the provider requires fragile scraping, unclear terms, or manual browser auth, stop and choose a licensed API or skip the feature.

## Risk And Complexity

Low risk:

- internal reactions and comments anchored to materialized objects
- share text/copy button
- portfolio-only discussion

Medium risk:

- public share pages with sanitized content
- OG image generation
- API endpoints for mobile clients

High risk:

- direct platform posting
- social sentiment ingestion
- trading-agent public sharing

Do not put high-risk work in the first implementation slice.

## Acceptance Criteria

First useful release:

- Home summary has reaction counts and a discussion thread.
- Stock Investigator summary has reaction counts and a ticker discussion thread.
- Users can copy/share sanitized summary text.
- All comments/reactions persist in Postgres.
- Social UI does not trigger AQL, LLM, market scans, or live social API calls.
- Trading Agent remains unchanged except for a documented follow-up to persist runs before social review.

Full release:

- Trading Agent candidates can be published to an internal review board.
- Share artifacts render safe OG pages.
- API exposes social state for iOS and agents.
- Optional social sentiment is materialized by scheduled jobs and routed through AQL/SAA evidence, not a parallel agent.

## Deployment Notes

Code changes touching Streamlit UI need dev UI deploy.

Code changes touching `auth_store`, shared services, API endpoints, or social share pages need:

- `scripts/which_deploy.sh --check ui`
- `scripts/which_deploy.sh --check api`
- relevant dev deploy scripts for changed containers

No prod deploy without explicit permission.
