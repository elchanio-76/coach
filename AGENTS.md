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

Current branch: `truecoach-login`.

The project has a working TrueCoach navigation and extraction scaffold:

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
- `DB design plan.md`: user-authored schema notes.
- `DB schema plan.md`: implementation-oriented schema plan.

Database direction:

- Persistence target is Postgres.
- Migrations will be managed with Alembic.
- Runtime database access and queries will be implemented with SQLAlchemy.
- Core source tables: `workouts`, `workout_items`, `workout_item_attachments`.
- Canonical `exercises` represent atomic movements only.
- Taxonomy applies to `workout_items`, not canonical exercises.
- `workout_item_exercises`, `workout_item_categories`, and `workout_item_metrics` are versioned assertion/proposal tables.
- Rejected and superseded AI assertions must remain stored for audit, few-shot examples, and future training data.

