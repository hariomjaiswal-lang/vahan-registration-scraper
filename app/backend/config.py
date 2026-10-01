"""Load and expose config.yaml as a plain dict with attribute-ish helpers.

`config.yaml` is the source of truth. A handful of values can be overridden from
the environment / project-root `.env` (loaded via backend.settings) so a machine
can differ without editing the committed YAML:

    VAHAN_PORTAL_URL                 -> portal.url
    VAHAN_HEADLESS (true/false)      -> browser.headless
    VAHAN_SLOW_MO (int ms)           -> browser.slow_mo
    VAHAN_CAPTCHA_MANUAL_FALLBACK    -> captcha.manual_fallback
    VAHAN_OUTPUT_DIR                 -> paths.output_dir
"""
from __future__ import annotations

import csv
import functools
import os
from pathlib import Path
from typing import Any

import yaml

from backend import settings as _settings  # noqa: F401  (ensures .env is loaded)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def _env_bool(name: str):
    v = os.getenv(name)
    return None if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@functools.lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    # ---- environment overrides -------------------------------------------
    if os.getenv("VAHAN_PORTAL_URL"):
        cfg.setdefault("portal", {})["url"] = os.environ["VAHAN_PORTAL_URL"]
    hl = _env_bool("VAHAN_HEADLESS")
    if hl is not None:
        cfg.setdefault("browser", {})["headless"] = hl
    if os.getenv("VAHAN_SLOW_MO"):
        cfg.setdefault("browser", {})["slow_mo"] = int(os.environ["VAHAN_SLOW_MO"])
    mf = _env_bool("VAHAN_CAPTCHA_MANUAL_FALLBACK")
    if mf is not None:
        cfg.setdefault("captcha", {})["manual_fallback"] = mf
    if os.getenv("VAHAN_OUTPUT_DIR"):
        cfg.setdefault("paths", {})["output_dir"] = os.environ["VAHAN_OUTPUT_DIR"]

    # ---- resolve relative paths against the project root ----------------
    paths = cfg.setdefault("paths", {})
    for key in ("output_dir", "logs_dir", "state_master"):
        if key in paths:
            paths[key] = str((ROOT / paths[key]).resolve())
    dl = cfg.setdefault("browser", {}).get("download_dir", "downloads")
    cfg["browser"]["download_dir"] = str((ROOT / dl).resolve())
    return cfg


def ensure_dirs() -> None:
    cfg = load()
    for p in (
        cfg["paths"]["output_dir"],
        cfg["paths"]["logs_dir"],
        cfg["browser"]["download_dir"],
    ):
        Path(p).mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def state_master() -> list[dict[str, str]]:
    """List of {state_name, state_code} rows from data/state_master.csv."""
    path = Path(load()["paths"]["state_master"])
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [row for row in csv.DictReader(fh)]


def state_code_for(name: str) -> str:
    """Best-effort map a live portal state label to its short code."""
    norm = _norm(name)
    for row in state_master():
        if _norm(row["state_name"]) == norm:
            return row["state_code"]
    # Fall back to a loose contains match (portal labels vary slightly).
    for row in state_master():
        if _norm(row["state_name"]) in norm or norm in _norm(row["state_name"]):
            return row["state_code"]
    # Last resort: first two alpha chars, uppercased.
    letters = "".join(c for c in name if c.isalpha())
    return letters[:2].upper() or "XX"


def _norm(s: str) -> str:
    return (
        s.lower()
        .replace("&", "and")
        .replace(".", "")
        .replace("-", " ")
        .replace("  ", " ")
        .strip()
    )
