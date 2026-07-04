# TrueCoach Scraper Plan

## Goal

Build a tool that can authenticate to `https://app.truecoach.co`, navigate the JavaScript-heavy app, and extract training data for later storage and AI-assisted coaching analysis.

## Current Status

Implemented:

1. Playwright-based login using credentials from `.env`.
2. Persisted authenticated browser storage for reuse.
3. Inspection artifacts: screenshots, rendered HTML, control dumps, and captured network responses.
4. Authenticated workout API fetch via `fetch-workouts`.
5. Parsed JSONL export via `parse-workouts`.
6. Postgres schema creation via Alembic.
7. Category seed import and parsed raw import via the DB CLI.

Verified CLI surface:

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

## Implemented Navigation and API Findings

1. Use Playwright browser automation rather than raw HTTP requests.
2. Log in with username/password credentials from environment variables.
3. Persist authenticated browser storage locally so later commands can reuse the session.
4. Capture basic inspection artifacts after login:
   - current URL
   - page title
   - visible links/buttons/inputs
   - screenshot
   - rendered HTML snapshot
5. Use artifacts to determine whether useful data is available through JSON API calls, embedded app state, or rendered DOM.

Credentials:

```bash
export TRUECOACH_EMAIL="..."
export TRUECOACH_PASSWORD="..."
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
```

## Current Findings

TrueCoach is an Ember app. Browser navigation is useful for login and endpoint discovery, but workout extraction should use the authenticated JSON API.

Past workout list route:

```text
https://app.truecoach.co/client/workouts?_=true&_page=3
```

The app loads workout data from:

```text
GET https://app.truecoach.co/proxy/api/clients/{client_id}/workouts?order=desc&page=1&per_page=30&states=completed%2Cmissed
```

Important request details:

- Authentication is stored in the `ember_simple_auth-session` cookie.
- API requests need the bearer token from that cookie.
- The workouts endpoint also requires the header `Role: Client`.
- The current `client_id` can be derived from `GET /proxy/api/users/{user_id}`.

Workout page response shape:

- `meta`: pagination with `page`, `per_page`, `total_count`, `total_pages`
- `workouts`: workout-level records with `id`, `due`, `state`, `rest_day`, warmup/cooldown fields, and `workout_item_ids`
- `workout_items`: movement/result records keyed by `workout_id`, with `name`, `info`, `result`, `position`, `state`, `exercise_id`, `is_circuit`, and `attachments`
- `comments`: comments associated with returned workouts

Current extraction command:

```bash
.venv/bin/coach fetch-workouts --pages 1
```

Raw API pages are saved to:

```text
data/cache/truecoach/api/workouts-client-{client_id}-page-{page}.json
```

## Parser Status

The parser preserves source text and avoids premature exercise normalization. Workout names, instructions, and results remain free-form text, and sparse TrueCoach `exercise_id` values are preserved separately.

Current parsed entities:

- `WorkoutRecord`
- `WorkoutItemRecord`
- `AttachmentRecord`
- `AnomalyRecord`

Current parsed outputs:

- `workouts.jsonl`
- `workout_items.jsonl`
- `attachments.jsonl`
- `anomalies.jsonl`
- `summary.json`

## Database Status

The database schema and bootstrap flow are implemented. See `DB schema plan.md` for the authoritative schema and import details.

Latest verified local bootstrap run:

- Seeded workout categories: `6`
- Imported workouts: `60`
- Imported workout items: `175`
- Imported attachments: `6`
- Imported canonical exercises: `13` when page 2 was added; canonical exercises are reused on pure reruns
- Imported exercise source aliases: `13` when page 2 was added; `1` was created during the earlier alias-migration rerun
- Imported TrueCoach exercise mappings: `64`

Current verified parsed dataset:

- `workouts-client-1172649-page-1.json`
- `workouts-client-1172649-page-2.json`

## Next Steps

1. Add source deletion detection for syncs instead of only upsert behavior.
2. Add richer incremental sync and resumability.
3. Add AI metric extraction proposals from `result_raw` and `info_raw` using the agreed constrained metric vocabulary.
4. Add review tooling for pending category, exercise, alias, and metric assertions.

Current AI exercise mapping status:

- Exercise mapping dry-run and write commands are implemented.
- Abbreviations are seeded from `exercise_abbreviations.json`.
- Run artifacts are written under `data/cache/truecoach/ai/exercise_mapping/active/`.
- Ollama exercise-mapping runs should use a context length of at least `16000` tokens for long workout-item prompts.
