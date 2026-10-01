"""Single background worker that executes queued flow runs one at a time."""
from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
import traceback

from backend import config
from backend.flows import REGISTRY
from backend.registry import Run, create_run, get_run, persist

_q: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_started = threading.Lock()


def _prepare_event_loop() -> None:
    """Playwright's sync API spawns the browser as a subprocess. On Windows that
    needs a ProactorEventLoop, but uvicorn installs the *Selector* policy, so a
    fresh loop created in this worker thread would raise NotImplementedError.
    Force a Proactor loop for this thread. (No-op elsewhere.)"""
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


_loop_kind = "?"


def _loop() -> None:
    global _loop_kind
    _prepare_event_loop()
    try:
        import asyncio as _a

        _l = _a.new_event_loop()
        _loop_kind = type(_l).__name__
        _l.close()
    except Exception as e:  # noqa: BLE001
        _loop_kind = f"err:{e}"
    while True:
        run_id = _q.get()
        run = get_run(run_id)
        if run is None:
            _q.task_done()
            continue
        fn = REGISTRY.get(run.flow)
        if fn is None:
            run.state = "error"
            run.error = f"Unknown flow '{run.flow}'"
            run.log(run.error, "error")
            _q.task_done()
            continue
        try:
            run.log(f"Starting {run.flow} (worker loop: {_loop_kind})")
            fn(run, config.load())
            if run.state not in ("cancelled", "error"):
                run.state = "done"
        except Exception as e:  # noqa: BLE001
            run.state = "error"
            run.error = f"{e}"
            run.log("Run crashed:\n" + traceback.format_exc(), "error")
        finally:
            run.finished = run.finished or time.time()
            persist(run)  # always capture the final state on disk
            _q.task_done()


def ensure_worker() -> None:
    global _worker
    with _started:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="vahan-worker", daemon=True)
            _worker.start()


def submit(flow: str, params: dict | None = None) -> Run:
    ensure_worker()
    run = create_run(flow, params or {})
    run.log("Queued")
    _q.put(run.id)
    return run
