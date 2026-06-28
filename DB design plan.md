# DB schema design notes

These are the user's initial notes about DB design.

This file is historical input only. The current source of truth is [DB schema plan.md](./DB%20schema%20plan.md), which reflects the implemented schema, import workflow, and deferred AI work.

Later decisions already made after these notes:

- Do not store raw parsed JSON snapshots per row in Postgres.
- Use a constrained shared `workout_item_metrics.metric_type` vocabulary.
- Introduce `exercise_source_aliases` for source-system exercise IDs instead of relying long-term on `exercises.tc_exercise_id`.

## Fields to keep

### workouts.jsonl

- Store data from workouts in a "workouts" table.
- client_id (keep for future multi-user support.)
- rest_day: (boolean) keep
- due: Keep Due date for workout (day it was programmed for & executed)
- Short_description: Keep. HTML description (not always useful, fall back if there's no other info in the details)
- state: "completed" or "missed". Store
- warmup/cooldown: should be null in most cases. don't store.

we also want the fields that allow linking with the detailed workouts:

- source_file
- source_id (this is the id of the workout), source_page, workout_item_ids
- uuid: This will be useful to avoid adding duplicate workouts in the DB (we will filter by due date when getting new workouts, but this should offer another layer of protection against duplication)

### workout_items.jsonl

- Early note: store workout item data separately from canonical exercises. Implemented schema uses `workout_items` as the main analytical table and `exercises` only for canonical atomic movements.
- "exercise_id": this is the truecoach exercise_id. Keep. Populate exercise names from the "name" property where id is not null. Should be unique.
- "name": Keep.
- "info" & "info_display" look exactly the same in the sample dataset. Keep one ("info")
- "result": Keep this (text, could be escaped unicode, or Greek or English).
- "state": Keep. "completed" or "missed"
- "source_id": Keep. this is the workout_item id in the original data. Useful to detect workout history if it's repeated exactly. Should not be the authoritative truth, sometimes the same exercises are retyped, often with small typos.
- workout_source_id: keep (this looks like a foreign key in the TrueCoach DB schema)

## General notes

1. Where we keep the original data from truecoach, the DB fields should be preceded by tc_ to distinguish them from our constructed fields.
2. Our DB tables should use their own primary keys, don't rely on the TrueCoach IDs as primary keys.
3. Index foreign key relationships between workouts and workout items for efficient querying.
4. Use UUIDs for unique identification across distributed systems and to prevent ID collisions when syncing with TrueCoach.
5. Normalize exercise names and details into a separate exercises table to avoid duplication and enable consistent reference across multiple workouts.
6. Store workout item order (position) explicitly to maintain sequence integrity when retrieving workout plans.
7. Keep attachment_count and source_file metadata in workout_items to track media assets and original data provenance.
8. Create indexes on state and due date columns for fast filtering and reporting queries. Client_id index is not required at this point (single user).
9. For attachments store URLs, not binary data.
10. Implement soft delete flags (deleted_at) instead of hard deletes to preserve workout history and allow recovery of accidentally deleted records.
11. Add a workout_category table with the following fields:
    - id: Primary key
    - name: Category name (e.g., Strength, Cardio, Mobility, Accessory, Crossfit WOD)
    - description: Text description of the category
    - color_code: Optional color code for UI display
    - created_at/updated_at: Timestamps
    - deleted_at: Soft delete flag
12. This evolved in the implemented design into `workout_item_categories`, because taxonomy applies to workout items, not canonical exercises.
13. Exercises table should store unique exercise definitions from TrueCoach:
    - id: Primary key
    - tc_exercise_id: Unique identifier from TrueCoach (exercise_id field)
    - name: Exercise name (from "name" property)
    - description: Text description of the exercise (if available)
    - created_at: Timestamp
    - updated_at: Timestamp
    - deleted_at: Soft delete flag
14. AI enrichment is deferred for now. The implemented schema supports versioned assertions in `workout_item_exercises`, `workout_item_categories`, and `workout_item_metrics`, but no AI workflow is wired yet.
15. Structured analytics are also deferred. The implemented target table for this concept is `workout_item_metrics`, not a separate `results` or `workout_performance` table.
