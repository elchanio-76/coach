from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import DEFAULT_BASE_URL, TrueCoachPaths


def _import_playwright() -> Any:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install project dependencies, then run "
            "`playwright install chromium` before using the TrueCoach CLI."
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "python-dotenv is not installed. Run `uv sync` to install project dependencies."
        ) from exc
    load_dotenv()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _launch_context(
    *,
    headless: bool,
    paths: TrueCoachPaths,
    use_storage: bool,
):
    sync_playwright, _ = _import_playwright()
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=headless)
    kwargs: dict[str, Any] = {"viewport": {"width": 1440, "height": 1000}}
    if use_storage and paths.storage_state.exists():
        kwargs["storage_state"] = str(paths.storage_state)
    context = browser.new_context(**kwargs)
    return playwright, browser, context


def login(
    *,
    base_url: str = DEFAULT_BASE_URL,
    paths: TrueCoachPaths | None = None,
    headless: bool = False,
    timeout_ms: int = 60_000,
) -> Path:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    _load_dotenv()
    email = _require_env("TRUECOACH_EMAIL")
    password = _require_env("TRUECOACH_PASSWORD")
    playwright, browser, context = _launch_context(
        headless=headless,
        paths=paths,
        use_storage=False,
    )
    try:
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
        _fill_first_available(
            page,
            [
                'input[type="email"]',
                'input[name="email"]',
                'input[name="username"]',
                'input[autocomplete="username"]',
                'input[placeholder*="email" i]',
            ],
            email,
            timeout_ms=timeout_ms,
        )
        _fill_first_available(
            page,
            [
                'input[type="password"]',
                'input[name="password"]',
                'input[autocomplete="current-password"]',
            ],
            password,
            timeout_ms=timeout_ms,
        )
        _click_first_available(
            page,
            [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Log in")',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
            ],
            timeout_ms=timeout_ms,
        )
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        context.storage_state(path=str(paths.storage_state))
        save_snapshot(page, paths=paths, label="login")
        return paths.storage_state
    finally:
        context.close()
        browser.close()
        playwright.stop()


def snapshot(
    *,
    url: str = DEFAULT_BASE_URL,
    paths: TrueCoachPaths | None = None,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> dict[str, Path]:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    playwright, browser, context = _launch_context(
        headless=headless,
        paths=paths,
        use_storage=True,
    )
    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        return save_snapshot(page, paths=paths, label="snapshot")
    finally:
        context.close()
        browser.close()
        playwright.stop()


def inspect(
    *,
    url: str = DEFAULT_BASE_URL,
    paths: TrueCoachPaths | None = None,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> Path:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    playwright, browser, context = _launch_context(
        headless=headless,
        paths=paths,
        use_storage=True,
    )
    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        payload = page.evaluate(
            """
            () => ({
              url: window.location.href,
              title: document.title,
              textSample: document.body.innerText.slice(0, 5000),
              links: Array.from(document.querySelectorAll('a')).slice(0, 200).map((node) => ({
                text: node.innerText.trim(),
                href: node.href
              })),
              buttons: Array.from(document.querySelectorAll('button')).slice(0, 200).map((node) => ({
                text: node.innerText.trim(),
                type: node.type || null,
                ariaLabel: node.getAttribute('aria-label')
              })),
              inputs: Array.from(document.querySelectorAll('input, textarea, select')).slice(0, 200).map((node) => ({
                tag: node.tagName.toLowerCase(),
                type: node.getAttribute('type'),
                name: node.getAttribute('name'),
                placeholder: node.getAttribute('placeholder'),
                autocomplete: node.getAttribute('autocomplete'),
                ariaLabel: node.getAttribute('aria-label')
              }))
            })
            """
        )
        output = paths.inspect_dir / f"inspect-{_timestamp()}.json"
        _write_json(output, payload)
        return output
    finally:
        context.close()
        browser.close()
        playwright.stop()


def capture(
    *,
    url: str = DEFAULT_BASE_URL,
    paths: TrueCoachPaths | None = None,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> dict[str, Path]:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    stamp = _timestamp()
    capture_dir = paths.network_dir / stamp
    capture_dir.mkdir(parents=True, exist_ok=True)
    responses: list[dict[str, Any]] = []
    playwright, browser, context = _launch_context(
        headless=headless,
        paths=paths,
        use_storage=True,
    )
    try:
        page = context.new_page()

        def on_response(response: Any) -> None:
            headers = response.headers
            content_type = headers.get("content-type", "")
            entry = {
                "url": response.url,
                "status": response.status,
                "method": response.request.method,
                "resourceType": response.request.resource_type,
                "contentType": content_type,
                "requestHeaders": _safe_request_headers(response.request.headers),
                "bodyPath": None,
            }
            if "json" in content_type:
                body_path = capture_dir / f"{len(responses):04d}-{_safe_response_name(response.url)}.json"
                try:
                    body_path.write_bytes(response.body())
                    entry["bodyPath"] = str(body_path)
                except Exception as exc:  # noqa: BLE001 - Playwright can fail for opaque/cached responses.
                    entry["bodyError"] = str(exc)
            responses.append(entry)

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        snapshot_paths = save_snapshot(page, paths=paths, label="capture")
        index_path = capture_dir / "responses.json"
        _write_json(
            index_path,
            {
                "url": page.url,
                "title": page.title(),
                "responses": responses,
            },
        )
        return {"network": index_path, **snapshot_paths}
    finally:
        context.close()
        browser.close()
        playwright.stop()


def save_snapshot(page: Any, *, paths: TrueCoachPaths, label: str) -> dict[str, Path]:
    stamp = _timestamp()
    screenshot_path = paths.screenshots_dir / f"{label}-{stamp}.png"
    html_path = paths.html_dir / f"{label}-{stamp}.html"
    meta_path = paths.inspect_dir / f"{label}-{stamp}.json"
    page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(page.content(), encoding="utf-8")
    _write_json(
        meta_path,
        {
            "url": page.url,
            "title": page.title(),
            "screenshot": str(screenshot_path),
            "html": str(html_path),
        },
    )
    return {"screenshot": screenshot_path, "html": html_path, "metadata": meta_path}


def _safe_response_name(url: str) -> str:
    parsed = urlparse(url)
    value = f"{parsed.netloc}{parsed.path}".strip("/") or "response"
    safe = "".join(char if char.isalnum() else "-" for char in value)
    return safe.strip("-")[:120] or "response"


def _safe_request_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"authorization", "cookie", "set-cookie"}
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def _fill_first_available(
    page: Any,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
) -> None:
    _, playwright_timeout = _import_playwright()
    last_error: Exception | None = None
    for selector in selectors:
        try:
            page.locator(selector).first.fill(value, timeout=timeout_ms // 4)
            return
        except playwright_timeout as exc:
            last_error = exc
    raise RuntimeError(f"Could not find a matching field for selectors: {selectors}") from last_error


def _click_first_available(
    page: Any,
    selectors: list[str],
    *,
    timeout_ms: int,
) -> None:
    _, playwright_timeout = _import_playwright()
    last_error: Exception | None = None
    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=timeout_ms // 4)
            return
        except playwright_timeout as exc:
            last_error = exc
    raise RuntimeError(f"Could not find a matching control for selectors: {selectors}") from last_error
