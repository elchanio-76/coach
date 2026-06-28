from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from . import api
from . import browser
from .db import create_engine, import_parsed_data, seed_workout_categories, session_scope
from . import parser as workout_parser
from .config import DEFAULT_BASE_URL, TrueCoachPaths


def main() -> None:
    parser = argparse.ArgumentParser(prog="coach", description="Coach project utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Log in to TrueCoach and save browser state")
    _add_common_options(login_parser, default_headless=False)

    snapshot_parser = subparsers.add_parser("snapshot", help="Save screenshot and HTML using saved browser state")
    _add_common_options(snapshot_parser, default_headless=True)

    inspect_parser = subparsers.add_parser("inspect", help="Dump visible page controls using saved browser state")
    _add_common_options(inspect_parser, default_headless=True)

    capture_parser = subparsers.add_parser("capture", help="Save page artifacts and JSON network responses")
    _add_common_options(capture_parser, default_headless=True)

    workouts_parser = subparsers.add_parser("fetch-workouts", help="Fetch raw workout pages from the TrueCoach API")
    workouts_parser.add_argument("--url", default=DEFAULT_BASE_URL, help="TrueCoach base URL")
    workouts_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/truecoach"),
        help="Directory containing storage state and API output",
    )
    workouts_parser.add_argument("--pages", type=int, default=1, help="Number of pages to fetch")
    workouts_parser.add_argument("--start-page", type=int, default=1, help="First page to fetch")
    workouts_parser.add_argument("--per-page", type=int, default=30, help="Workouts per page")
    workouts_parser.add_argument("--states", default="completed,missed", help="Comma-separated workout states")

    parse_parser = subparsers.add_parser("parse-workouts", help="Parse raw workout API pages into JSONL records")
    parse_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/truecoach"),
        help="Directory containing API input and parsed output",
    )
    parse_parser.add_argument(
        "--input",
        type=Path,
        action="append",
        default=None,
        help="Raw workout API JSON file. May be passed more than once. Defaults to all cached workout pages.",
    )
    parse_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for parsed JSONL output. Defaults to data/cache/truecoach/parsed.",
    )

    db_upgrade_parser = subparsers.add_parser("db-upgrade", help="Apply Alembic migrations to the configured database")
    db_upgrade_parser.add_argument("--revision", default="head", help="Alembic revision target")

    seed_categories_parser = subparsers.add_parser("db-seed-categories", help="Seed workout categories")
    seed_categories_parser.add_argument(
        "--categories-file",
        type=Path,
        default=Path("workout_categories.json"),
        help="Path to category seed JSON",
    )

    import_parsed_parser = subparsers.add_parser("db-import-parsed", help="Import parsed TrueCoach data into Postgres")
    import_parsed_parser.add_argument(
        "--parsed-dir",
        type=Path,
        default=Path("data/cache/truecoach/parsed"),
        help="Directory containing workouts.jsonl, workout_items.jsonl, and attachments.jsonl",
    )

    bootstrap_parser = subparsers.add_parser("db-bootstrap", help="Run migrations, seed categories, and import parsed data")
    bootstrap_parser.add_argument(
        "--categories-file",
        type=Path,
        default=Path("workout_categories.json"),
        help="Path to category seed JSON",
    )
    bootstrap_parser.add_argument(
        "--parsed-dir",
        type=Path,
        default=Path("data/cache/truecoach/parsed"),
        help="Directory containing parsed JSONL files",
    )

    args = parser.parse_args()
    paths = TrueCoachPaths(cache_dir=args.cache_dir) if hasattr(args, "cache_dir") else TrueCoachPaths()

    try:
        if args.command == "login":
            output = browser.login(
                base_url=args.url,
                paths=paths,
                headless=args.headless,
                timeout_ms=args.timeout_ms,
            )
            print(f"Saved browser state: {output}")
        elif args.command == "snapshot":
            outputs = browser.snapshot(
                url=args.url,
                paths=paths,
                headless=args.headless,
                timeout_ms=args.timeout_ms,
            )
            for name, path in outputs.items():
                print(f"{name}: {path}")
        elif args.command == "inspect":
            output = browser.inspect(
                url=args.url,
                paths=paths,
                headless=args.headless,
                timeout_ms=args.timeout_ms,
            )
            print(f"Inspection output: {output}")
        elif args.command == "capture":
            outputs = browser.capture(
                url=args.url,
                paths=paths,
                headless=args.headless,
                timeout_ms=args.timeout_ms,
            )
            for name, path in outputs.items():
                print(f"{name}: {path}")
        elif args.command == "fetch-workouts":
            outputs = api.fetch_workout_pages(
                paths=paths,
                base_url=args.url,
                pages=args.pages,
                start_page=args.start_page,
                per_page=args.per_page,
                states=args.states,
            )
            for path in outputs:
                print(f"workouts: {path}")
        elif args.command == "parse-workouts":
            outputs = workout_parser.parse_cached_workouts(
                paths=paths,
                input_files=args.input,
                output_dir=args.output_dir,
            )
            for name, path in outputs.items():
                print(f"{name}: {path}")
        elif args.command == "db-upgrade":
            _run_db_upgrade(args.revision)
            print(f"Database upgraded to {args.revision}")
        elif args.command == "db-seed-categories":
            engine = create_engine()
            with session_scope(engine) as session:
                count = seed_workout_categories(session, args.categories_file)
            print(f"Seeded workout categories: {count}")
        elif args.command == "db-import-parsed":
            engine = create_engine()
            with session_scope(engine) as session:
                summary = import_parsed_data(session, args.parsed_dir)
            print(f"Imported workouts: {summary.workouts}")
            print(f"Imported workout items: {summary.workout_items}")
            print(f"Imported attachments: {summary.attachments}")
            print(f"Imported exercises: {summary.exercises}")
            print(f"Imported workout item exercises: {summary.workout_item_exercises}")
        elif args.command == "db-bootstrap":
            _run_db_upgrade("head")
            engine = create_engine()
            with session_scope(engine) as session:
                categories = seed_workout_categories(session, args.categories_file)
                summary = import_parsed_data(session, args.parsed_dir)
            print(f"Seeded workout categories: {categories}")
            print(f"Imported workouts: {summary.workouts}")
            print(f"Imported workout items: {summary.workout_items}")
            print(f"Imported attachments: {summary.attachments}")
            print(f"Imported exercises: {summary.exercises}")
            print(f"Imported workout item exercises: {summary.workout_item_exercises}")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _add_common_options(parser: argparse.ArgumentParser, *, default_headless: bool) -> None:
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="TrueCoach URL to open")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/truecoach"),
        help="Directory for storage state and debug artifacts",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="Playwright timeout in milliseconds")
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", action="store_true", default=default_headless)
    headless_group.add_argument("--headed", action="store_false", dest="headless")


def _run_db_upgrade(revision: str) -> None:
    config = Config(str(Path("alembic.ini")))
    command.upgrade(config, revision)


if __name__ == "__main__":
    main()
