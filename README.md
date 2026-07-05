# Coach

Tools for collecting and analyzing personal training data.

## Status

- TrueCoach login, authenticated API fetch, and parsed JSONL export are implemented.
- Postgres schema creation, category/abbreviation seeding, and parsed data import are implemented.
- AI category assignment dry-run and pending DB writes are implemented.
- AI exercise mapping dry-run and pending DB writes are implemented.
- AI metric extraction and review workflows are not implemented yet.

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

Seed exercise abbreviations from `exercise_abbreviations.json`:

```bash
.venv/bin/coach db-seed-exercise-abbreviations
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
The current abbreviation seed file is `exercise_abbreviations.json`.
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
For Ollama, set the model context length high enough for long workout descriptions. A context length of `16000` tokens is the current working setting; the default `4096` can fail when prompt plus response exceeds the limit.

Generate dry-run category proposals for uncategorized workout items:

```bash
.venv/bin/coach ai-category-assignment-dry-run --limit 10
```

By default, selection skips workout items that already have a current pending or approved category assertion.
Use a bounded local ID window to process records in batches:

```bash
.venv/bin/coach ai-category-assignment-dry-run --min-workout-item-id 1 --max-workout-item-id 25
```

Use `--workout-item-id` one or more times to force explicit rechecks, including IDs outside the current window.

Write pending AI category assertions to Postgres:

```bash
.venv/bin/coach ai-category-assignment-write --min-workout-item-id 1 --max-workout-item-id 25
```

Default run artifacts are written under `data/cache/truecoach/ai/category_assignment/active/`.
Archive a reviewed batch when you are done with it:

```bash
.venv/bin/coach ai-category-assignment-archive-run --run-dir data/cache/truecoach/ai/category_assignment/active/20260704T000000Z
```

Reruns are idempotent for identical current pending assertions and supersede older current pending AI assertions when the proposal changes.

## AI Exercise Mapping

Exercise mapping uses the same AI routing variables as category assignment:

```bash
AI_PROVIDER="ollama"
MODEL="gemma4:12b"
AI_URL="http://localhost:11434"
```

For Ollama, configure the selected model or runner with a context length of at least `16000` tokens. Some workout items include long descriptions and candidate context, and the default `4096` context can produce token-limit failures.

Observed local model tradeoffs:

- `gemma4:12b` has produced the most accurate results so far, but it is slow. It is a thinking model and spends noticeably more time deliberating before returning structured output.
- `gemma4:e4b` has been the best speed/accuracy tradeoff for this workflow so far. It is non-thinking, faster, and still generally reliable for exercise classification.
- Smaller local models are more likely to return malformed JSON, wrong data types, or hallucinated canonical exercise IDs.

Seed abbreviations before running the mapper:

```bash
.venv/bin/coach db-seed-exercise-abbreviations
```

Generate dry-run exercise proposals:

```bash
.venv/bin/coach ai-exercise-mapping-dry-run --limit 10
```

Write pending AI exercise assertions to Postgres:

```bash
.venv/bin/coach ai-exercise-mapping-write --min-workout-item-id 1 --max-workout-item-id 25
```

By default, selection skips workout items that already have a current pending or approved AI exercise assertion. Existing TrueCoach exercise mappings are included as context but do not block processing, so the mapper can add missing movements from free-form workout text.

The mapper can create pending canonical `exercises` rows for newly identified movements and pending `exercise_name_aliases` rows for synonym judgments. New canonical exercise names are normalized before insert so lowercase-only model output does not go into the database verbatim.

The workflow retries once after an exercise-mapping error. This is intended to recover from one-off malformed responses such as wrong JSON shape, wrong data types, placeholder/non-exercise rows, or hallucinated IDs. If the second attempt fails, the item is left in the run artifacts for manual review. The parser rejects placeholder names such as `None`/`null` and non-exercise rows like pacing or quality notes before they reach the database. Run artifacts are written under `data/cache/truecoach/ai/exercise_mapping/active/`.

Archive a reviewed batch when you are done with it:

```bash
.venv/bin/coach ai-exercise-mapping-archive-run --run-dir data/cache/truecoach/ai/exercise_mapping/active/20260704T000000Z
```

## End-to-End Workflow

```bash
.venv/bin/coach login
.venv/bin/coach fetch-workouts --pages 1
.venv/bin/coach parse-workouts
.venv/bin/coach db-bootstrap
```
