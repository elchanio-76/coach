# Coach

Tools for collecting and analyzing personal training data.

## Status

- TrueCoach login, authenticated API fetch, and parsed JSONL export are implemented.
- Postgres schema creation, category seeding, and parsed data import are implemented.
- AI category assignment dry-run and pending DB writes are implemented.
- AI exercise mapping, metric extraction, and review workflows are not implemented yet.

## TrueCoach Navigation

Install dependencies and the Chromium browser used by Playwright:

```bash
uv sync
uv run playwright install chromium
```

Set credentials locally:

```bash
TRUECOACH_EMAIL="..."
TRUECOACH_PASSWORD="..."
```

The CLI reads these from `.env`. Existing shell environment variables take precedence.

Run the first milestone commands:

```bash
.venv/bin/coach login
.venv/bin/coach snapshot
.venv/bin/coach inspect
.venv/bin/coach capture --url 'https://app.truecoach.co/client/workouts?_=true&_page=3'
.venv/bin/coach fetch-workouts --pages 1
.venv/bin/coach parse-workouts
```

Generated browser state and inspection artifacts are written to `data/cache/truecoach/`.
Parsed JSONL records are written to `data/cache/truecoach/parsed/`.

## Database

Set `DBURL` in `.env` to your local Postgres database.

Apply the schema:

```bash
.venv/bin/coach db-upgrade
```

Seed categories from `workout_categories.json`:

```bash
.venv/bin/coach db-seed-categories
```

Import parsed TrueCoach data:

```bash
.venv/bin/coach db-import-parsed
```

Run the full setup in one step:

```bash
.venv/bin/coach db-bootstrap
```

The importer reads parsed seed data from `data/cache/truecoach/parsed/`.
The current category seed file is `workout_categories.json`.
Database imports are designed to be rerun safely through upsert-style behavior.

Latest verified parsed/imported dataset:

- `60` workouts
- `175` workout items
- `6` attachments
- `64` TrueCoach exercise mappings

When page 2 was added and imported, the alias-aware importer created `13` new canonical exercises and `13` new `exercise_source_aliases`.

## AI Category Assignment

Set AI routing in `.env`:

```bash
AI_PROVIDER="ollama"
MODEL="llama3.1"
AI_URL="http://localhost:11434"
```

Use `OPENAI_API_KEY` when `AI_PROVIDER="openai"`.

Generate dry-run category proposals for uncategorized workout items:

```bash
.venv/bin/coach ai-category-assignment-dry-run --limit 10
```

Artifacts are written under `data/cache/truecoach/ai/category_assignment/`.

Write pending AI category assertions to Postgres:

```bash
.venv/bin/coach ai-category-assignment-write --limit 10
```

Reruns are idempotent for identical current pending assertions and supersede older current pending AI assertions when the proposal changes.

## End-to-End Workflow

```bash
.venv/bin/coach login
.venv/bin/coach fetch-workouts --pages 1
.venv/bin/coach parse-workouts
.venv/bin/coach db-bootstrap
```
