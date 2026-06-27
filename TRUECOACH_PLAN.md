# TrueCoach Scraper Plan

## Goal

Build a tool that can authenticate to `https://app.truecoach.co`, navigate the JavaScript-heavy app, and extract training data for later storage and AI-assisted coaching analysis.

## Milestone 1: Authenticated Navigation

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

## Initial CLI

```bash
coach login
coach snapshot
coach inspect
```

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

## Later Milestones

1. Build a parser from raw workout pages into normalized Python records.
2. Design the database schema from the observed API shape.
3. Store raw source payloads alongside parsed records for audit/reprocessing.
4. Add an AI-assisted categorization agent after the parser/database foundation is stable.
5. Add rate limiting, resumability, and incremental sync.

## Parser Plan

The parser should preserve source text and avoid premature exercise normalization. Workout names, workout instructions, and logged results are free-form text, and TrueCoach `exercise_id` is sparse.

Initial parsed entities:

- `WorkoutRecord`: one row per workout with source ID, due date, state, rest day flag, title, program name, warmup/cooldown text, source timestamps, and raw source payload reference.
- `WorkoutItemRecord`: one row per ordered workout item with source item ID, workout source ID, position, name, prescribed `info`, logged `result`, item state, circuit flag, TrueCoach exercise ID when present, selected exercises, and raw attachments.
- `AttachmentRecord`: one row per workout item attachment with source item ID, name, URL, MIME type, and size.

Parser responsibilities:

1. Read one or more raw API page JSON files.
2. Validate required keys: `meta`, `workouts`, `workout_items`.
3. Build workout records keyed by `workout.id`.
4. Attach item records to workouts using `workout_item_ids` and `workout_id`.
5. Preserve all free-form strings exactly, plus trimmed display variants where useful.
6. Emit deterministic JSONL/CSV summaries for inspection before database import.
7. Report anomalies, such as missing item IDs, duplicate IDs, orphan items, unknown states, and malformed attachment data.

Later AI categorization:

After raw parsing and storage, create an AI agent that reviews uncategorized workout items. The agent should map each item to an existing exercise and category where appropriate, or propose/create a new exercise/category entry when no match exists. Initial categories and seed exercises will be user-defined before enabling this workflow.
