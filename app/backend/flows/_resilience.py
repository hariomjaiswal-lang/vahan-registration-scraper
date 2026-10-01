"""Shared per-item failure recovery for the flows.

A single network blip already retries inside solve_captcha_and_apply / an
outer try wraps the whole loop so the run always reaches packaging (see
backend/portal.py and each flow). This adds the missing middle layer: when the
*page itself* goes stale (e.g. the machine slept for hours and the tab never
recovered), every remaining item in that state fails instantly - one after
another - because a basic selector like #rtoCode can't be found anymore.

`note_failure()` counts consecutive per-item failures and, once a threshold is
hit, reopens the report page and re-applies filters/selection before letting
the loop continue - so a dead page costs a handful of items, not the rest of
the run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from backend.portal import PortalError


def _looks_complete(files_dir: Path, min_files: int) -> bool:
    """A run counts as a usable reconciliation baseline only if it both (a)
    downloaded a realistic number of files - not a 1-state smoke test - and
    (b) actually reached Stage 8 and produced a zip. (b) matters separately
    from (a): a run whose *process* was killed mid-flight (machine slept /
    backend restarted) leaves its downloaded files on disk with no exception
    ever raised to trigger the "package what we have" safety net, so it can
    have plenty of files yet never finish - that run must not be picked over
    an older one that actually completed."""
    if sum(1 for _ in files_dir.glob("*.xlsx")) < min_files:
        return False
    return any(files_dir.parent.glob("*.zip"))


def find_flow1_dir(output_dir: str, min_files: int = 30) -> Path | None:
    """The Flow 1 output to reconcile against.

    Picks the most recent run whose files/ folder looks like a **complete**
    36-state pull (>= min_files files, and it actually finished - see
    _looks_complete) - not just the most recent Flow 1 folder, which is very
    often a small ad-hoc smoke test (--states GA), or an interrupted run,
    either of which would silently corrupt every reconciliation check."""
    cands = sorted(Path(output_dir).glob("*_flow1_*/files"), key=lambda p: p.stat().st_mtime)
    full = [c for c in cands if _looks_complete(c, min_files)]
    if full:
        return full[-1]
    return cands[-1] if cands else None


def find_flow3_dir(output_dir: str, min_files: int = 300) -> Path | None:
    """The Flow 3 output to reconcile Flow 4 against - same idea as
    find_flow1_dir: prefer the most recent run that both looks like a real
    pull (a full Flow 3 run is roughly 1,700 files; a smoke test is a
    handful) and actually finished (see _looks_complete), falling back to the
    most recent one if nothing meets the bar."""
    cands = sorted(Path(output_dir).glob("*_flow3_*/files"), key=lambda p: p.stat().st_mtime)
    full = [c for c in cands if _looks_complete(c, min_files)]
    if full:
        return full[-1]
    return cands[-1] if cands else None


def group_flow3_by_state(flow3_dir: Path) -> dict[str, list[Path]]:
    """{state_code: [Monthwise_YYYY-<state>-<rto>.xlsx, ...]} for every RTO
    file in a Flow 3 run - the baseline Flow 4 reconciles against."""
    out: dict[str, list[Path]] = {}
    for f in Path(flow3_dir).glob("Monthwise_*-*-*.xlsx"):
        parts = f.stem.split("-")
        if len(parts) >= 3:
            out.setdefault(parts[1], []).append(f)
    return out


def assert_scope(header: str, required: list[str], what: str) -> None:
    """Raise PortalError if the report header doesn't mention every string in
    `required` - a sign the State/RTO selection silently didn't apply, so the
    report (and the file we're about to download) covers a *wider* scope than
    requested (e.g. a whole state instead of one RTO) with no visible error.
    Call this right after Apply succeeds, before download_excel."""
    missing = [r for r in required if r.lower() not in header.lower()]
    if missing:
        raise PortalError(
            f"scope check failed for {what}: header is missing {missing} "
            f"(got {header[:160]!r}) - the filter probably didn't apply"
        )


def note_failure(portal, run, consecutive: int, on_recover: Callable[[], None], threshold: int = 3) -> int:
    """Call from a per-item `except` block. Returns the updated consecutive-
    failure count (reset to 0 after a recovery attempt, successful or not -
    never blocks the loop)."""
    consecutive += 1
    if consecutive >= threshold:
        run.log(
            f"{consecutive} items in a row failed - the page may be stale "
            "(e.g. the machine slept); reopening it",
            "warn",
        )
        try:
            portal.open_report_page()
            on_recover()
            run.log("Recovered: report page reopened", "info")
        except Exception as e:  # noqa: BLE001 - recovery is best-effort
            run.log(f"Recovery attempt failed: {e}", "error")
        consecutive = 0
    return consecutive


def note_success(_consecutive: int) -> int:
    return 0
