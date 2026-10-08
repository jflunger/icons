"""Load and validate config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = ["MIT", "ISC", "Apache-2.0", "CC0-1.0", "CC-BY-4.0"]


@dataclass
class SetConfig:
    prefix: str
    enabled: bool = True
    keep_sizes: list[int] = field(default_factory=list)


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    basic_auth: str = ""
    cart_password: str = ""
    png_size: int = 512
    data_dir: Path = ROOT / "data"
    schedule: str = "weekly"
    license_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWLIST))
    sets: dict[str, SetConfig] = field(default_factory=dict)
    synonyms_path: Path = ROOT / "synonyms.toml"

    @property
    def enabled_sets(self) -> list[SetConfig]:
        return [s for s in self.sets.values() if s.enabled]

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "icons.db"

    @property
    def new_db_path(self) -> Path:
        return self.data_dir / "icons.new.db"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def cart_path(self) -> Path:
        return self.data_dir / "cart.json"


def load(path: Path | str | None = None) -> Config:
    path = Path(path or os.environ.get("ICONSEARCH_CONFIG") or ROOT / "config.toml")
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    server = raw.get("server", {})
    ingest = raw.get("ingest", {})

    cfg = Config()
    cfg.host = str(server.get("host", cfg.host))
    cfg.port = int(server.get("port", cfg.port))
    cfg.basic_auth = os.environ.get("ICONSEARCH_AUTH", str(server.get("basic_auth", "")))
    cfg.png_size = int(server.get("png_size", cfg.png_size))
    # The cart password stays out of git: env var, else secrets/cart_password (gitignored).
    secret = path.parent / "secrets" / "cart_password"
    cfg.cart_password = os.environ.get("ICONSEARCH_CART_PASSWORD") or (
        secret.read_text().strip() if secret.exists() else "")

    data_dir = Path(os.environ.get("ICONSEARCH_DATA") or ingest.get("data_dir", "data"))
    cfg.data_dir = data_dir if data_dir.is_absolute() else path.parent / data_dir
    cfg.schedule = str(ingest.get("schedule", cfg.schedule))
    if cfg.schedule not in ("weekly", "daily", "off"):
        raise ValueError(f"ingest.schedule must be weekly, daily or off, not {cfg.schedule!r}")
    cfg.license_allowlist = list(ingest.get("license_allowlist", cfg.license_allowlist))
    cfg.synonyms_path = path.parent / "synonyms.toml"

    for prefix, opts in raw.get("sets", {}).items():
        if not isinstance(opts, dict):
            raise ValueError(f"[sets.{prefix}] must be a table")
        cfg.sets[prefix] = SetConfig(
            prefix=prefix,
            enabled=bool(opts.get("enabled", True)),
            keep_sizes=[int(x) for x in opts.get("keep_sizes", [])],
        )
    return cfg
