"""FastAPI backend: control API for the VAHAN scraper + serves the static
frontend (the React dashboard in ../frontend).

Frontend and backend are separate folders. This one server hosts both:
  * JSON API under /api/*
  * everything else -> ../frontend/ (index.html, styles.css, app.js)

CORS is enabled so the frontend can also be hosted elsewhere (e.g. a static
server on another port) and still call this API.
"""
from __future__ import annotations

import csv
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config, runner
from backend.portal import PortalError, VahanPortal
from backend.registry import get_run, list_runs, load_persisted, request_cancel, solve_captcha
from backend.settings import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
STATE_MASTER_CSV = PROJECT_ROOT / "data" / "state_master.csv"

app = FastAPI(title="VAHAN Registration Scraper")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

FLOWS = [
    {
        "id": "flow1_state_monthwise",
        "name": "Flow 1 - Vahan_StateMonthwise",
        "description": "36 States/UTs, Maker x Month-wise, calendar year. "
        "One Excel per state + All-India reconciliation.",
        "params": {
            "states": "optional list of state names/codes to limit the run",
            "skip_all_india": "bool - skip the All-India reference download",
            "skip_reconciliation": "bool - skip the MB/BMW check",
        },
    },
    {
        "id": "flow2_state_fuelwise",
        "name": "Flow 2 - Vahan_StateFuelwise",
        "description": "36 States/UTs x each month Jan..now, Maker x Fuel, "
        "'1 Month Flexible' period. One Excel per State-month; reconciled "
        "against the latest Flow 1 output.",
        "params": {
            "states": "optional list of state names/codes to limit the run",
            "months": "optional list e.g. ['SEP'] or [9] - default Jan..current month",
            "year": "optional int - default current year",
            "flow1_dir": "optional path to a Flow 1 run's files/ folder (else auto-found)",
            "skip_reconciliation": "bool",
        },
    },
    {
        "id": "flow3_rto_monthwise",
        "name": "Flow 3 - Vahan_RTO-Monthwise",
        "description": "Every RTO within every State/UT (~1,700 total), Maker x "
        "Month-wise, calendar year. One Excel per State-RTO; reconciled against "
        "the latest Flow 1 output. Runs for hours unless limited - use this for "
        "the scheduled thrice-monthly run, not ad hoc.",
        "params": {
            "states": "optional list of state names/codes to limit the run",
            "rtos": "optional list of RTO codes to limit the run (smoke test)",
            "max_rtos_per_state": "optional int cap per state (smoke test)",
            "flow1_dir": "optional path to a Flow 1 run's files/ folder (else auto-found)",
            "skip_reconciliation": "bool",
        },
    },
    {
        "id": "flow4_rto_fuelwise",
        "name": "Flow 4 - Vahan_RTOFuelwise",
        "description": "Every RTO x every month Jan..now (~1,700 RTOs x months - "
        "the largest flow by far), Maker x Fuel, '1 Month Flexible' period. One "
        "Excel per State-RTO-month; reconciled against the latest Flow 3 output. "
        "Always test with rtos/max_rtos_per_state/months before a full run.",
        "params": {
            "states": "optional list of state names/codes to limit the run",
            "rtos": "optional list of RTO codes to limit the run (smoke test)",
            "max_rtos_per_state": "optional int cap per state (smoke test)",
            "months": "optional list e.g. ['SEP'] or [9] - default Jan..current month",
            "year": "optional int - default current year",
            "flow3_dir": "optional path to a Flow 3 run's files/ folder (else auto-found)",
            "skip_reconciliation": "bool",
        },
    },
]


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    n = load_persisted()
    if n:
        print(f"[registry] restored {n} run(s) from disk")
    runner.ensure_worker()


# ---------------------------------------------------------------- API
@app.get("/api/flows")
def api_flows():
    return FLOWS


# Windows Scheduled Task name(s) for each flow - set once a flow has a task
# registered (see run-flow*-scheduled.cmd + schtasks). Flow 3/4 run "thrice a
# month" as three separate single-day monthly tasks (schtasks.exe doesn't
# reliably accept a comma list of days for /D on a MONTHLY schedule). None =
# not set up yet.
SCHEDULED_TASKS: dict[str, list[str] | None] = {
    "flow1_state_monthwise": ["VahanScraper Flow1 Daily"],
    "flow2_state_fuelwise": ["VahanScraper Flow2 Daily"],
    "flow3_rto_monthwise": ["VahanScraper Flow3 Day5", "VahanScraper Flow3 Day15", "VahanScraper Flow3 Day25"],
    "flow4_rto_fuelwise": ["VahanScraper Flow4 Day6", "VahanScraper Flow4 Day16", "VahanScraper Flow4 Day26"],
}


def _query_task(task_name: str) -> dict:
    """Ask Windows Task Scheduler for one task's status - real state, not a
    cached guess, so the dashboard never shows a schedule that silently
    stopped working."""
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/v", "/fo", "LIST"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:  # noqa: BLE001
        return {"configured": False, "error": str(e)}
    if out.returncode != 0:
        return {"configured": False}
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return {
        "configured": True,
        "status": fields.get("Status"),
        "schedule_type": fields.get("Schedule Type"),
        "start_time": fields.get("Start Time"),
        "next_run_time": fields.get("Next Run Time"),
        "last_run_time": fields.get("Last Run Time"),
        "last_result": fields.get("Last Result"),
    }


def _parse_task_time(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d-%m-%Y %H:%M:%S")
    except ValueError:
        return None


@app.get("/api/schedules")
def api_schedules():
    out = {}
    for flow_id, task_names in SCHEDULED_TASKS.items():
        if not task_names:
            out[flow_id] = {"configured": False}
            continue
        occurrences = [{"task_name": n, **_query_task(n)} for n in task_names]
        configured = [o for o in occurrences if o.get("configured")]
        if not configured:
            out[flow_id] = {"configured": False}
            continue
        # Represent the flow by whichever occurrence fires next.
        soonest = min(
            configured,
            key=lambda o: _parse_task_time(o.get("next_run_time")) or datetime.max,
        )
        out[flow_id] = {**soonest, "occurrences": len(task_names)}
    return out


@app.get("/api/states")
def api_states():
    """[{code, name}] for the state/RTO picker dropdown - static list, no
    portal round-trip needed. Same 36 States/UTs the flows validate against."""
    with open(STATE_MASTER_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted(
        [{"code": r["state_code"], "name": r["state_name"]} for r in rows],
        key=lambda s: s["name"],
    )


_rto_cache: dict[str, list[dict]] = {}  # state code -> [{value, code, name}], cleared on restart


@app.get("/api/rtos")
def api_rtos(states: str):
    """{state_code: [{value, code, name}]} for the RTO picker - fetched live
    from the portal (RTOs aren't static; the portal is the only source of
    truth) and cached in memory per state so re-opening the same state's
    dropdown doesn't hit the portal again this session."""
    codes = [s.strip().upper() for s in states.split(",") if s.strip()]
    if not codes:
        return {}
    missing = [c for c in codes if c not in _rto_cache]
    if missing:
        cfg = config.load()
        try:
            with VahanPortal(cfg) as portal:
                portal.open_report_page()
                portal.apply_common_filters(calendar_year=True)
                for code in missing:
                    portal.select_only_state(code)
                    _rto_cache[code] = portal.rto_options()
        except PortalError as e:
            raise HTTPException(502, f"Could not fetch RTOs from the portal: {e}")
    return {code: _rto_cache.get(code, []) for code in codes}


class StartRun(BaseModel):
    flow: str
    params: dict = {}


@app.post("/api/runs")
def api_start(body: StartRun):
    if body.flow not in {f["id"] for f in FLOWS}:
        raise HTTPException(400, f"Unknown flow '{body.flow}'")
    run = runner.submit(body.flow, body.params)
    return run.summary()


@app.get("/api/runs")
def api_runs():
    return list_runs()


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run.summary()


@app.get("/api/runs/{run_id}/logs")
def api_logs(run_id: str, after: int = 0):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return {"after": after, "lines": [l.as_dict() for l in run.logs[after:]], "total": len(run.logs)}


class CaptchaSolution(BaseModel):
    text: str


@app.post("/api/runs/{run_id}/captcha")
def api_captcha(run_id: str, body: CaptchaSolution):
    if not solve_captcha(run_id, body.text):
        raise HTTPException(409, "no captcha pending for this run")
    return {"ok": True}


@app.post("/api/runs/{run_id}/cancel")
def api_cancel(run_id: str):
    if not request_cancel(run_id):
        raise HTTPException(404, "no such run")
    return {"ok": True}


@app.get("/api/runs/{run_id}/download")
def api_download(run_id: str, which: str = "zip"):
    run = get_run(run_id)
    if not run or not run.result:
        raise HTTPException(404, "no result yet")
    path = run.result.get(which)
    if not path or not Path(path).exists():
        raise HTTPException(404, f"'{which}' not available")
    return FileResponse(path, filename=Path(path).name)


# ---------------------------------------------------------------- static frontend
# Optional convenience: when VAHAN_SERVE_FRONTEND is true (the default) this one
# server hosts the dashboard too. Set it false to run the frontend on its own
# server (python -m tools.serve_frontend) - the API then only speaks JSON.
if settings.serve_frontend and FRONTEND_DIR.is_dir():
    # Mounted last so /api/* routes above win; html=True serves index.html at "/".
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return {
            "service": "vahan-scraper-api",
            "docs": "/docs",
            "frontend": "served separately - run `python -m tools.serve_frontend`",
        }
