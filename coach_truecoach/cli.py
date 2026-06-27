from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import api
from . import browser
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

    args = parser.parse_args()
    paths = TrueCoachPaths(cache_dir=args.cache_dir)

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


if __name__ == "__main__":
    main()
