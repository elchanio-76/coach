from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_URL = "https://app.truecoach.co"
DEFAULT_CACHE_DIR = Path("data/cache/truecoach")


@dataclass(frozen=True)
class TrueCoachPaths:
    cache_dir: Path = DEFAULT_CACHE_DIR

    @property
    def storage_state(self) -> Path:
        return self.cache_dir / "storage_state.json"

    @property
    def screenshots_dir(self) -> Path:
        return self.cache_dir / "screenshots"

    @property
    def html_dir(self) -> Path:
        return self.cache_dir / "html"

    @property
    def inspect_dir(self) -> Path:
        return self.cache_dir / "inspect"

    @property
    def network_dir(self) -> Path:
        return self.cache_dir / "network"

    @property
    def api_dir(self) -> Path:
        return self.cache_dir / "api"

    def ensure(self) -> None:
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.inspect_dir.mkdir(parents=True, exist_ok=True)
        self.network_dir.mkdir(parents=True, exist_ok=True)
        self.api_dir.mkdir(parents=True, exist_ok=True)
