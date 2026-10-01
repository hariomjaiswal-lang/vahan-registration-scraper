"""Thread-safe state shared between the FastAPI layer and the background scraper
worker: run status, per-run logs, and the manual-captcha fallback queue.

Runs are also persisted to `runs/<id>.json` so they survive a backend restart
(the dashboard reads them back on startup). One run executes at a time.
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

RunState = Literal["queued", "running", "captcha_wait", "done", "error", "cancelled"]

_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = _ROOT / "runs"
_KEEP = 300  # newest N run files kept on disk

_lock = threading.RLock()
_runs: dict[str, "Run"] = {}
_id_seq = itertools.count(1)
_cancel_flags: dict[str, threading.Event] = {}
_last_persist: dict[str, float] = {}
_external: set[str] = set()  # run ids this process didn't create itself (see sync_external_runs)
_synced_mtime: dict[str, float] = {}


@dataclass
class LogLine:
    ts: float
    level: str
    msg: str

    def as_dict(self) -> dict:
        return {"ts": self.ts, "level": self.level, "msg": self.msg}


@dataclass
class CaptchaRequest:
    run_id: str
    image_b64: str
    ocr_guess: str
    attempt: int
    created: float = field(default_factory=time.time)
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    solution: str | None = None

    def resolve(self, text: str) -> None:
        self.solution = text.strip()
        self._event.set()

    def wait(self, timeout: float) -> str | None:
        self._event.wait(timeout)
        return self.solution


@dataclass
class Run:
    id: str
    flow: str
    params: dict
    state: RunState = "queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    progress_done: int = 0
    progress_total: int = 0
    current_item: str = ""
    result: dict = field(default_factory=dict)
    error: str | None = None
    logs: list[LogLine] = field(default_factory=list)
    captcha: CaptchaRequest | None = None
    # "dashboard" = created via the API/worker, which owns its whole lifecycle
    # in this process - if this process dies mid-run, the run really is dead.
    # "cli" = started by tools.run_flow (e.g. the daily scheduled task) as its
    # own separate process - this backend restarting says nothing about
    # whether that process is still alive, so load_persisted() must not mark
    # it interrupted just because the dashboard happened to restart.
    source: str = "dashboard"

    def log(self, msg: str, level: str = "info") -> None:
        with _lock:
            self.logs.append(LogLine(time.time(), level, msg))
            if len(self.logs) > 5000:
                self.logs = self.logs[-4000:]
        _maybe_persist(self)

    def to_dict(self) -> dict:
        """Full snapshot for on-disk persistence (includes logs; not the
        transient captcha)."""
        d = self.summary()
        d.pop("captcha", None)
        d["logs"] = [l.as_dict() for l in self.logs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        run = cls(id=d["id"], flow=d["flow"], params=d.get("params") or {})
        run.state = d.get("state", "error")
        run.created = d.get("created") or time.time()
        run.started = d.get("started")
        run.finished = d.get("finished")
        run.progress_done = d.get("progress_done", 0)
        run.progress_total = d.get("progress_total", 0)
        run.current_item = d.get("current_item", "")
        run.result = d.get("result") or {}
        run.error = d.get("error")
        run.logs = [LogLine(x["ts"], x["level"], x["msg"]) for x in d.get("logs", [])]
        run.source = d.get("source", "dashboard")
        return run

    def summary(self) -> dict:
        return {
            "id": self.id,
            "flow": self.flow,
            "params": self.params,
            "state": self.state,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "progress_done": self.progress_done,
            "progress_total": self.progress_total,
            "current_item": self.current_item,
            "result": self.result,
            "error": self.error,
            "source": self.source,
            "captcha": (
                {
                    "image_b64": self.captcha.image_b64,
                    "ocr_guess": self.captcha.ocr_guess,
                    "attempt": self.captcha.attempt,
                }
                if self.captcha and self.state == "captcha_wait"
                else None
            ),
        }


# --------------------------------------------------------------- persistence
def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def persist(run: "Run") -> None:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=RUNS_DIR, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(run.to_dict(), fh)
        os.replace(tmp, _run_path(run.id))
        _last_persist[run.id] = time.time()
    except Exception:  # persistence must never break a run
        pass
    # Back up to Blob Storage only once a run reaches a terminal state - not
    # on every ~3s debounced write (Flow 4 alone persists hundreds of times
    # over its ~2.5 day runtime; backing up every one of those would be pure
    # waste). A no-op locally where Blob Storage isn't configured.
    if run.state in ("done", "error", "cancelled"):
        try:
            from backend import cloud_storage

            cloud_storage.upload_bytes(
                json.dumps(run.to_dict()).encode("utf-8"), f"runs/{run.id}.json"
            )
        except Exception:
            pass


def _maybe_persist(run: "Run", every: float = 3.0) -> None:
    if time.time() - _last_persist.get(run.id, 0.0) >= every:
        persist(run)


def load_persisted() -> int:
    """Read runs/*.json back into memory on startup. Runs that were mid-flight
    when the process died are marked 'error' (the worker is gone)."""
    if not RUNS_DIR.is_dir():
        return 0
    # persist()'s atomic write leaves a .tmp-<random>.json next to the real
    # file for the instant between writing and the rename; a process killed
    # in exactly that window (e.g. `Stop-Process -Force` on a dev restart)
    # leaves it orphaned. Sweep those on startup - each one is a duplicate,
    # stale snapshot of a real run's id, which previously made that run show
    # up multiple times at different progress values in the dashboard.
    for stale in RUNS_DIR.glob(".tmp-*.json"):
        stale.unlink(missing_ok=True)
    files = sorted(RUNS_DIR.glob("[0-9]*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[:-_KEEP]:  # prune oldest beyond the cap
        old.unlink(missing_ok=True)
    n = 0
    with _lock:
        for f in files[-_KEEP:]:
            try:
                run = Run.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
            if run.source == "dashboard" and run.state in ("queued", "running", "captcha_wait"):
                # Only a dashboard-owned run's lifecycle lived entirely in the
                # process that just restarted - a "cli" run (the scheduled
                # task) is a separate process that may well still be alive;
                # sync_external_runs() keeps it updated instead.
                run.state = "error"
                run.error = (run.error or "") + "\n(interrupted - backend restarted)"
                run.finished = run.finished or time.time()
            if run.source != "dashboard":
                _external.add(run.id)
                _synced_mtime[run.id] = f.stat().st_mtime
            _runs[run.id] = run
            _cancel_flags.setdefault(run.id, threading.Event())
            n += 1
    return n


def create_run(flow: str, params: dict, source: str = "dashboard") -> Run:
    with _lock:
        rid = f"{int(time.time())}-{next(_id_seq)}"
        run = Run(id=rid, flow=flow, params=params, source=source)
        _runs[rid] = run
        _cancel_flags[rid] = threading.Event()
    persist(run)
    return run


def sync_external_runs() -> None:
    """Pick up runs this backend process didn't start itself - e.g. the daily
    scheduled task, which launches `tools.run_flow` as its own separate
    process. That script writes to the same runs/*.json files, but this
    process's own in-memory `_runs` never hears about it, so without this the
    dashboard would show nothing for a scheduled run until the next restart.

    Safe by construction: a run id already known to this process AND not
    previously flagged external is never touched here, so a run this
    process's own worker is actively driving can't be clobbered by a stale
    on-disk snapshot (the worker's debounced writes lag its live state)."""
    if not RUNS_DIR.is_dir():
        return
    with _lock:
        for f in RUNS_DIR.glob("[0-9]*.json"):  # excludes orphaned .tmp-*.json (see load_persisted)
            rid = f.stem
            is_new = rid not in _runs
            if not is_new and rid not in _external:
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if not is_new and _synced_mtime.get(rid) == mtime:
                continue
            try:
                run = Run.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
            _runs[rid] = run
            _cancel_flags.setdefault(rid, threading.Event())
            _external.add(rid)
            _synced_mtime[rid] = mtime


def get_run(run_id: str) -> Run | None:
    sync_external_runs()
    with _lock:
        return _runs.get(run_id)


def list_runs() -> list[dict]:
    sync_external_runs()
    with _lock:
        return [r.summary() for r in sorted(_runs.values(), key=lambda r: r.created, reverse=True)]


def request_cancel(run_id: str) -> bool:
    with _lock:
        ev = _cancel_flags.get(run_id)
        if ev:
            ev.set()
            return True
        return False


def cancel_requested(run_id: str) -> bool:
    with _lock:
        ev = _cancel_flags.get(run_id)
        return bool(ev and ev.is_set())


def open_captcha(run: Run, image_bytes: bytes, ocr_guess: str, attempt: int) -> CaptchaRequest:
    req = CaptchaRequest(
        run_id=run.id,
        image_b64=base64.b64encode(image_bytes).decode("ascii"),
        ocr_guess=ocr_guess,
        attempt=attempt,
    )
    with _lock:
        run.captcha = req
        run.state = "captcha_wait"
    persist(run)
    return req


def close_captcha(run: Run, back_to: RunState = "running") -> None:
    with _lock:
        run.captcha = None
        if run.state == "captcha_wait":
            run.state = back_to
    persist(run)


def solve_captcha(run_id: str, text: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
        if not run or not run.captcha:
            return False
        req = run.captcha
    req.resolve(text)
    return True
