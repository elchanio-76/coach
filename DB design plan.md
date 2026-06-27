# DB schema design notes

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

- Store workout_item data in an "exercises" table
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
12. Add a exercise_category table to handle many-to-many relationships between workout items and categories:
    - id: Primary key
    - exercise_id: Foreign key referencing exercises.id
    - category_id: Foreign key referencing workout_categories.id
    - created_at: Timestamp
    - deleted_at: Soft delete flag
13. Exercises table should store unique exercise definitions from TrueCoach:
    - id: Primary key
    - tc_exercise_id: Unique identifier from TrueCoach (exercise_id field)
    - name: Exercise name (from "name" property)
    - description: Text description of the exercise (if available)
    - created_at: Timestamp
    - updated_at: Timestamp
    - deleted_at: Soft delete flag
14. The exercise name and descriptions should be reviewed by an AI agent to ensure consistency and accuracy. The agent will determine the exercise name and category based on the description and available data, for incoming exercises that have no exercise_id (where the tc_exercise_id is null)
15. Once the AI agent determines the exercise name and category, populate the exercises table with the new entry and create the corresponding relationship in the exercise_categories table.
16. An AI agent should also analyze the results to extract structured performance metrics (e.g., max weight, reps, failure points) and populate a dedicated "workout_performance" table for detailed analytics and progress tracking.
17. The analytics should be stored in a results table which tracks individual workout session metrics, including:
    - id: Primary key
    - workout_item_id: Foreign key referencing exercises.id
    - workout_id: Foreign key referencing workouts.id
    - metric_type: Type of metric recorded (e.g., max_weight, reps, failure_point)
    - value: Numeric value of the metric
    - unit: Unit of measurement (kg, lbs, reps, rounds)
    - timestamp: When the result was recorded (this should be the due date of the workout)
    - source_data: Original unprocessed result text from TrueCoach
    - created_at/updated_at: Timestamps
    - deleted_at: Soft delete flag
