# Database Schema Plan

This plan converts the TrueCoach API data into durable source tables, then layers human/AI enrichment on top as versioned assertions.

## Design Principles

1. Keep TrueCoach source fields with a `tc_` prefix.
2. Use local primary keys for all tables; never use TrueCoach IDs as local primary keys.
3. Preserve raw free-form text exactly.
4. Treat `workout_items` as the main analytical unit.
5. Treat `exercises` as canonical atomic movements only.
6. Apply taxonomy to `workout_items`, not canonical exercises.
7. Store AI/user enrichment as versioned assertions, including rejected and superseded rows.
8. Store attachment URLs, not binary files.
9. Prefer append/update-safe imports so sync can be rerun.

## Core Source Tables

### workouts

One row per TrueCoach workout.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `tc_workout_id`: TrueCoach workout ID, unique, not null
- `tc_uuid`: TrueCoach UUID, unique when present
- `tc_client_id`: TrueCoach client ID
- `tc_source_file`: source cache file path
- `tc_source_page`: source API page number
- `due_date`: programmed workout date
- `state`: `completed`, `missed`, etc.
- `rest_day`: boolean
- `title`: nullable
- `program_id`: nullable
- `program_name`: nullable
- `short_description_html`: nullable
- `tc_workout_item_ids`: JSONB array of TrueCoach workout item IDs
- `tc_comment_ids`: JSONB array
- `tc_created_at`: source timestamp
- `tc_updated_at`: source timestamp
- `created_at`
- `updated_at`
- `deleted_at`

Constraints and indexes:

- unique `tc_workout_id`
- unique `tc_uuid` where not null
- index `due_date`
- index `state`

### workout_items

One row per item/block inside a workout. This is not a canonical exercise.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `workout_id`: FK to `workouts.id`
- `tc_workout_item_id`: TrueCoach workout item ID, unique, not null
- `tc_workout_id`: denormalized TrueCoach workout ID for import/debugging
- `tc_exercise_id`: nullable TrueCoach exercise ID
- `tc_source_file`
- `tc_source_page`
- `position`: item order inside workout
- `name_raw`: exact source name
- `name_display`: trimmed/display name
- `info_raw`: exact prescription/instruction text
- `info_display`: trimmed/display prescription text
- `result_raw`: exact logged result text
- `result_display`: trimmed/display result text
- `state`: `completed`, `missed`, etc.
- `is_circuit`: boolean
- `selected_exercises`: JSONB
- `linked`: boolean
- `assessment_id`: nullable
- `request_video`: boolean
- `attachment_count`: integer
- `exercise_id`: nullable FK to `exercises.id` only when there is a single clear canonical movement; many-to-many remains authoritative
- `tc_created_at`: source timestamp
- `created_at`
- `updated_at`
- `deleted_at`

Constraints and indexes:

- unique `tc_workout_item_id`
- FK `workout_id`
- index `(workout_id, position)`
- index `tc_exercise_id`
- index `exercise_id`
- index `state`

### workout_item_attachments

One row per attachment URL on a workout item.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `workout_item_id`: FK to `workout_items.id`
- `tc_workout_item_id`: denormalized source ID
- `name`
- `url`
- `mime_type`
- `size_bytes`
- `tc_source_file`
- `tc_source_page`
- `created_at`
- `updated_at`
- `deleted_at`

Constraints and indexes:

- unique `(workout_item_id, url)`
- index `workout_item_id`

## Canonical Tables

### exercises

Canonical atomic movements only. Examples: `Back Squat`, `Run`, `Overhead Squat`, `Push Press`, `Box Jump Over`.

Do not use this table for workout formats like `AMRAP 20`, `5 rounds of`, `WOD`, or `Every 2 for 16`.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `name`: canonical exercise name
- `description`: nullable
- `tc_exercise_id`: nullable, unique where present
- `created_by_source`: `truecoach`, `ai`, `user`, `system`
- `review_status`: `pending`, `approved`, `rejected`, `superseded`
- `created_at`
- `updated_at`
- `deleted_at`

Constraints and indexes:

- unique lower-normalized `name` eventually, after deciding normalization rules
- unique `tc_exercise_id` where not null
- index `review_status`

### workout_categories

Taxonomy labels applied to workout items.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `name`: category name
- `description`
- `color_code`
- `created_at`
- `updated_at`
- `deleted_at`

Constraints and indexes:

- unique lower-normalized `name`

## Versioned Enrichment Tables

These tables store assertions/proposals. Rejected and superseded rows stay in the database for audit, examples, and future model training.

Common columns:

- `source`: `truecoach`, `ai`, `user`, `system`
- `confidence`: nullable numeric from 0.0 to 1.0
- `review_status`: `pending`, `approved`, `rejected`, `superseded`
- `is_current`: boolean
- `superseded_by_id`: nullable self-reference
- `model_name`: nullable
- `model_version`: nullable
- `rationale`: nullable
- `created_at`
- `updated_at`
- `reviewed_at`: nullable
- `reviewed_by`: nullable

Normal app queries should use:

```sql
review_status = 'approved' AND is_current = true
```

### workout_item_exercises

Many-to-many mapping from a workout item to canonical atomic exercises.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `workout_item_id`: FK to `workout_items.id`
- `exercise_id`: FK to `exercises.id`
- `position`: order within the workout item
- `role`: nullable, e.g. `primary`, `secondary`, `component`
- common versioned enrichment columns

Constraints and indexes:

- index `workout_item_id`
- index `exercise_id`
- partial index for current approved rows on `(workout_item_id, exercise_id)` where `is_current = true AND review_status = 'approved'`

### workout_item_categories

Many-to-many taxonomy assignment for workout items.

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `workout_item_id`: FK to `workout_items.id`
- `category_id`: FK to `workout_categories.id`
- common versioned enrichment columns

Constraints and indexes:

- index `workout_item_id`
- index `category_id`
- partial index for current approved rows on `(workout_item_id, category_id)` where `is_current = true AND review_status = 'approved'`

### workout_item_metrics

Versioned structured metrics extracted from `workout_items.result_raw` and sometimes `info_raw`.

Examples:

- `best_successful_weight = 65 kg`
- `failed_weight = 70 kg`
- `rounds_completed = 6`
- `extra_reps = 10 cal`
- `time_cap = 15 min`

Columns:

- `id`: local primary key
- `uuid`: local UUID
- `workout_item_id`: FK to `workout_items.id`
- `metric_type`: text
- `value_numeric`: nullable numeric
- `value_text`: nullable text
- `unit`: nullable text
- `source_text`: original source text span or full result text
- `occurred_on`: usually `workouts.due_date`
- common versioned enrichment columns

Constraints and indexes:

- index `workout_item_id`
- index `metric_type`
- index `occurred_on`
- partial index for current approved rows on `(workout_item_id, metric_type)` where `is_current = true AND review_status = 'approved'`

## Import Operations

### Raw Workout Import

Input:

- `data/cache/truecoach/parsed/workouts.jsonl`
- `data/cache/truecoach/parsed/workout_items.jsonl`
- `data/cache/truecoach/parsed/attachments.jsonl`

Steps:

1. Upsert `workouts` by `tc_workout_id`.
2. Update mutable source fields on conflict: state, short description, source timestamps, item IDs, etc.
3. Upsert `workout_items` by `tc_workout_item_id`.
4. Resolve `workout_items.workout_id` by `workouts.tc_workout_id`.
5. Upsert attachments by `(workout_item_id, url)`.
6. Do not delete rows that are absent from a later sync until we intentionally implement source deletion detection.

### Seed Exercise Import

Input:

- User-provided initial exercise list.
- TrueCoach `tc_exercise_id` values where present.

Steps:

1. Insert approved user seed exercises.
2. For workout items with `tc_exercise_id`, create or update matching `exercises` where useful.
3. Create `workout_item_exercises` rows from TrueCoach IDs with `source = 'truecoach'`, `review_status = 'approved'`, `is_current = true`.

### Seed Category Import

Input:

- User-provided initial category list.

Steps:

1. Insert categories by normalized name.
2. Categories are not automatically applied to exercises.
3. Future AI/user workflow applies categories to workout items through `workout_item_categories`.

## AI Augmentation Operations

### Exercise Mapping Agent

For each workout item with no approved current exercise mapping:

1. Read `name_raw`, `info_raw`, `result_raw`, surrounding workout date/state, and existing exercises.
2. Propose zero or more canonical exercises.
3. If no match exists, propose a new exercise with `review_status = 'pending'`.
4. Insert `workout_item_exercises` assertions with `source = 'ai'`, confidence, rationale, model metadata, and `review_status = 'pending'`.

### Category Agent

For each workout item with no approved current category:

1. Read the full workout item context.
2. Propose one or more workout item categories.
3. Insert `workout_item_categories` assertions as pending versioned rows.

### Metrics Agent

For each completed workout item with non-empty `result_raw`:

1. Read `info_raw` and `result_raw`.
2. Extract structured metrics as versioned assertions.
3. Preserve ambiguous or mixed values as `value_text` when `value_numeric` is not reliable.
4. Insert rows as pending unless high-confidence automated approval is explicitly enabled later.

## Review Operations

Approval flow:

1. User approves a pending assertion.
2. Mark competing current assertions for the same scope as `superseded` and `is_current = false` when appropriate.
3. Mark approved assertion as `review_status = 'approved'`, `is_current = true`, `reviewed_at`, `reviewed_by`.

Rejection flow:

1. Mark assertion as `review_status = 'rejected'`, `is_current = false`.
2. Keep rationale and source data for later evaluation/training.

Correction flow:

1. Insert a new user assertion.
2. Supersede older current assertion through `superseded_by_id`.
3. Keep all historical rows.

## Open Implementation Choices

1. Whether to use PostgreSQL enums or text plus check constraints for states and review statuses.
2. Whether to store raw parsed JSON snapshots per row as `jsonb` for easier reprocessing.
3. Whether `workout_items.exercise_id` should exist as a convenience FK or be removed to force all exercise linkage through `workout_item_exercises`.
4. Exact category seed list.
5. Exact metric type vocabulary.
