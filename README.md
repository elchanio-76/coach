# Coach

Tools for collecting and analyzing personal training data.

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
