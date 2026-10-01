"""Environment-driven settings, read from the project-root `.env` (plus real
environment variables, which win over the file).

`.env` holds host/port, CORS, and secrets (SMTP) - things that are
machine-specific or must not be committed. Portal behaviour, filters and
selectors stay in `config.yaml`; a few of those can also be overridden from the
environment (see `backend/config.py`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # no-op if the file is absent


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: list[str]) -> list[str]:
    v = os.getenv(name)
    if not v:
        return default
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class SMTP:
    host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    username: str = field(default_factory=lambda: os.getenv("SMTP_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    sender: str = field(default_factory=lambda: os.getenv("SMTP_FROM", ""))
    recipients: list[str] = field(default_factory=lambda: _list("SMTP_TO", []))
    starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and self.recipients)


@dataclass
class Settings:
    # ---- API server ----
    host: str = field(default_factory=lambda: os.getenv("VAHAN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("VAHAN_PORT", "8000")))
    reload: bool = field(default_factory=lambda: _bool("VAHAN_RELOAD", False))
    cors_origins: list[str] = field(default_factory=lambda: _list("VAHAN_CORS_ORIGINS", ["*"]))
    # When false, the API does NOT serve the frontend - run it separately with
    # `python -m tools.serve_frontend`.
    serve_frontend: bool = field(default_factory=lambda: _bool("VAHAN_SERVE_FRONTEND", True))

    # ---- standalone frontend server (tools/serve_frontend.py) ----
    frontend_host: str = field(default_factory=lambda: os.getenv("FRONTEND_HOST", "127.0.0.1"))
    frontend_port: int = field(default_factory=lambda: int(os.getenv("FRONTEND_PORT", "5173")))
    # If set, the frontend server injects this as API_BASE (overrides config.js),
    # e.g. "http://localhost:8000".
    api_base: str = field(default_factory=lambda: os.getenv("VAHAN_API_BASE", ""))

    smtp: SMTP = field(default_factory=SMTP)


settings = Settings()
