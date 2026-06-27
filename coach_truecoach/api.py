from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen

from .config import DEFAULT_BASE_URL, TrueCoachPaths


@dataclass(frozen=True)
class SessionAuth:
    access_token: str
    user_id: int
    cookie_header: str


class TrueCoachApiClient:
    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, paths: TrueCoachPaths | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.paths = paths or TrueCoachPaths()
        self.auth = load_session_auth(self.paths.storage_state)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.base_url}{path}{query}",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Authorization": f"Bearer {self.auth.access_token}",
                "Cookie": self.auth.cookie_header,
                "Referer": f"{self.base_url}/client/workouts",
                "Role": "Client",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"TrueCoach API request failed: HTTP {exc.code} for {request.full_url}") from exc
        except URLError as exc:
            raise RuntimeError(f"TrueCoach API request failed: {exc.reason}") from exc

    def current_client_id(self) -> int:
        payload = self.get_json(f"/proxy/api/users/{self.auth.user_id}")
        user = payload.get("user") or {}
        client_id = user.get("client_id")
        if not client_id:
            clients = payload.get("clients") or []
            if clients:
                client_id = clients[0].get("id")
        if not client_id:
            raise RuntimeError("Could not determine TrueCoach client ID from current user payload")
        return int(client_id)

    def fetch_workouts(
        self,
        *,
        client_id: int,
        page: int,
        per_page: int = 30,
        states: str = "completed,missed",
    ) -> dict[str, Any]:
        return self.get_json(
            f"/proxy/api/clients/{client_id}/workouts",
            {
                "order": "desc",
                "page": page,
                "per_page": per_page,
                "states": states,
            },
        )


def fetch_workout_pages(
    *,
    paths: TrueCoachPaths | None = None,
    base_url: str = DEFAULT_BASE_URL,
    pages: int = 1,
    start_page: int = 1,
    per_page: int = 30,
    states: str = "completed,missed",
) -> list[Path]:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    client = TrueCoachApiClient(base_url=base_url, paths=paths)
    client_id = client.current_client_id()
    outputs: list[Path] = []
    if pages < 1:
        raise RuntimeError("--pages must be at least 1")
    if start_page < 1:
        raise RuntimeError("--start-page must be at least 1")
    for page in range(start_page, start_page + pages):
        payload = client.fetch_workouts(
            client_id=client_id,
            page=page,
            per_page=per_page,
            states=states,
        )
        output = paths.api_dir / f"workouts-client-{client_id}-page-{page}.json"
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        outputs.append(output)
        total_pages = int((payload.get("meta") or {}).get("total_pages") or page)
        if page >= total_pages:
            break
    return outputs


def load_session_auth(storage_state_path: Path) -> SessionAuth:
    if not storage_state_path.exists():
        raise RuntimeError(f"Missing browser state file: {storage_state_path}. Run `coach login` first.")
    payload = json.loads(storage_state_path.read_text(encoding="utf-8"))
    cookies = [
        f"{cookie['name']}={cookie['value']}"
        for cookie in payload.get("cookies", [])
        if "truecoach.co" in cookie.get("domain", "")
    ]
    cookie_header = "; ".join(cookies)
    for cookie in payload.get("cookies", []):
        if cookie.get("name") != "ember_simple_auth-session":
            continue
        session = json.loads(unquote(cookie["value"]))
        authenticated = session.get("authenticated") or {}
        access_token = authenticated.get("access_token")
        user_id = authenticated.get("user_id")
        if access_token and user_id:
            return SessionAuth(
                access_token=access_token,
                user_id=int(user_id),
                cookie_header=cookie_header,
            )
    raise RuntimeError("Could not find Ember auth session in saved browser state")
