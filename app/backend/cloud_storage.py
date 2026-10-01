"""Azure Blob Storage backup layer.

Playwright's browser downloads land on local disk first - there's no way to
stream a browser download straight into Blob Storage - so every flow still
works exactly as it does today, writing to the container's local disk during
a run. This module backs up the durable artifacts (the zip, the summary, and
each run's status record) to Blob Storage at the points they're already
finalized, so they survive an App Service restart/redeploy even though the
local disk underneath a run is not guaranteed to.

No-ops entirely (and logs nothing) when AZURE_STORAGE_CONNECTION_STRING isn't
set, so local development is completely unaffected - this only activates in
the deployed environment.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "vahan-output")


@functools.lru_cache(maxsize=1)
def _client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return None
    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn_str)
        try:
            svc.create_container(CONTAINER)
        except Exception:
            pass  # already exists
        return svc
    except Exception:
        return None


def enabled() -> bool:
    return _client() is not None


def upload_file(local_path: Path, blob_name: str) -> None:
    """Best-effort: a failed backup must never fail the run itself."""
    svc = _client()
    if not svc:
        return
    try:
        with open(local_path, "rb") as fh:
            svc.get_blob_client(CONTAINER, blob_name).upload_blob(fh, overwrite=True)
    except Exception:
        pass


def upload_bytes(data: bytes, blob_name: str) -> None:
    svc = _client()
    if not svc:
        return
    try:
        svc.get_blob_client(CONTAINER, blob_name).upload_blob(data, overwrite=True)
    except Exception:
        pass


def download_to_file(blob_name: str, local_path: Path) -> bool:
    """Used as a fallback when a finished run's local file is gone (e.g. after
    a restart moved us to a fresh container) but its blob backup still exists."""
    svc = _client()
    if not svc:
        return False
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as fh:
            data = svc.get_blob_client(CONTAINER, blob_name).download_blob()
            data.readinto(fh)
        return True
    except Exception:
        return False
