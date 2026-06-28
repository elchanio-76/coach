# Coach

Project to scrape and analyze training data from TrueCoach, store in a database and add metadata and semantic meaning for subsequent analysis by AI.

## Tech Stack

front-end: Web (Streamlit or Flask)
data analysis back-end: Python
Persistence layer: Postgres + local file cache where simpler

## Instructions

1. When working in a new feature, create a new worktree or branch (name it with a two-word name after the requested feature). If in doubt whether to create a new branch, ask me.
2. ALWAYS make sure you are located in the correct branch before making any changes.
3. ALWAYS make sure you work in a Python virtual environment before making changes. DO NOT install or update system-wide packages. 
4. Use uv as a Python package and environment manager. 
5. Don't be overly verbose. Avoid platitudes and compliments. I prefer short and precise communication.

## Current Status

Current branch: `database-schema`.

The project has a working TrueCoach navigation, extraction, and database bootstrap scaffold:

- Playwright browser automation logs in to `https://app.truecoach.co`.
- Credentials are read from `.env` via `python-dotenv`.
- Authenticated browser state is saved to `data/cache/truecoach/storage_state.json`.
- TrueCoach is an Ember app. Browser automation is useful for login and endpoint discovery, but workout extraction should use the authenticated JSON API.
- The important workouts endpoint is:

```text
GET /proxy/api/clients/{client_id}/workouts?order=desc&page=1&per_page=30&states=completed%2Cmissed
```

Important API details:

- Bearer token and user ID come from the `ember_simple_auth-session` cookie in Playwright storage state.
- The workouts endpoint also requires header `Role: Client`.
- The current client ID is derived from `GET /proxy/api/users/{user_id}`.

Implemented CLI commands:

```bash
.venv/bin/coach login
.venv/bin/coach snapshot
.venv/bin/coach inspect
.venv/bin/coach capture --url 'https://app.truecoach.co/client/workouts?_=true&_page=3'
.venv/bin/coach fetch-workouts --pages 1
.venv/bin/coach parse-workouts
.venv/bin/coach db-upgrade
.venv/bin/coach db-seed-categories
.venv/bin/coach db-import-parsed
.venv/bin/coach db-bootstrap
```

Local artifacts:

```text
data/cache/truecoach/
  storage_state.json
  screenshots/
  html/
  inspect/
  network/
  api/
  parsed/
```

Parser status:

- Raw API pages are saved to `data/cache/truecoach/api/workouts-client-{client_id}-page-{page}.json`.
- Parsed JSONL files are written to `data/cache/truecoach/parsed/`.
- Current parsed outputs:
  - `workouts.jsonl`
  - `workout_items.jsonl`
  - `attachments.jsonl`
  - `anomalies.jsonl`
  - `summary.json`
- The parser preserves raw free-form workout names, instructions, and results. Do not prematurely normalize exercise text.

Planning documents:

- `TRUECOACH_PLAN.md`: scraping/auth/API/parser plan and TrueCoach endpoint findings.
- `DB schema plan.md`: authoritative schema and import workflow document.
- `DB design plan.md`: historical user-authored schema notes.

Database status:

- Persistence target is Postgres.
- Alembic is configured and the initial migration exists.
- Runtime database access and imports are implemented with SQLAlchemy.
- Core source tables exist: `workouts`, `workout_items`, `workout_item_attachments`.
- Canonical `exercises` represent atomic movements only.
- Taxonomy applies to `workout_items`, not canonical exercises.
- Versioned enrichment tables exist: `workout_item_exercises`, `workout_item_categories`, and `workout_item_metrics`.
- `exercise_source_aliases` is implemented for canonical exercise mapping from source-system exercise IDs.
- Rejected and superseded AI assertions must remain stored for audit, few-shot examples, and future training data.
- Category seed import from `workout_categories.json` is implemented.
- Parsed raw import from `data/cache/truecoach/parsed/` is implemented.
- `DBURL` is read from `.env`; Postgres URLs are normalized to `postgresql+psycopg` at runtime when needed.

Latest verified local bootstrap run:

- Seeded workout categories: `6`
- Imported workouts: `60`
- Imported workout items: `175`
- Imported attachments: `6`
- Imported canonical exercises: `13` when page 2 was added; canonical exercises are reused on pure reruns
- Imported exercise source aliases: `13` when page 2 was added; `1` was created during the earlier alias-migration rerun
- Imported TrueCoach exercise mappings: `64`

Known implementation details:

- The importer currently reads parsed seed files from `data/cache/truecoach/parsed/`.
- The current verified parsed dataset spans `workouts-client-1172649-page-1.json` and `workouts-client-1172649-page-2.json`.
- AI enrichment tables exist, but no AI write/review workflow is implemented yet.
- The importer now resolves source exercise IDs through `exercise_source_aliases` instead of `exercises.tc_exercise_id`.
- Multiple TrueCoach exercise IDs can map to one canonical exercise through `exercise_source_aliases`.
- Raw parsed JSON snapshots are intentionally not stored per row in Postgres.
- The agreed `workout_item_metrics.metric_type` v1 vocabulary is: `best_successful_weight`, `failed_weight`, `reps_completed`, `sets_completed`, `rounds_completed`, `extra_reps`, `distance_completed`, `duration_completed`, `time_to_complete`, `time_cap`, `calories_completed`.
