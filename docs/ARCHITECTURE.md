# Architecture

Current, accurate reference for how the VAHAN scraper is built and deployed.
(`docs/OVERVIEW.md` predates Flows 2-4, email, scheduling, self-heal, and the
monolithic restructure below — treat this file as the source of truth.)

---

## 1. Shape: one monolithic app

Everything runs as a **single Python process**: one FastAPI server that both
exposes the JSON API (`/api/*`) and serves the dashboard (static
`frontend/`) on one port. There is no separate frontend server and no
microservices — this was a deliberate choice (over a split API + static-host
setup) so the whole thing is one self-contained unit: one folder to zip, one
Docker build context, one process to deploy to Azure App Service.

```
Browser  ──HTTP──>  uvicorn (backend/main.py)
                      ├── GET /                → StaticFiles(frontend/)
                      ├── /api/runs, /api/states, /api/schedules, ...
                      └── background worker (backend/runner.py)
                             └── Playwright (Chromium) → VAHAN portal
```

Only one dashboard-started run executes at a time (`runner.py`'s single
worker); scheduled/CLI runs (`tools/run_flow.py`) launch as independent OS
processes and are picked up live by the dashboard via
`registry.sync_external_runs()` — they don't block or compete with a
dashboard run.

---

## 2. Directory structure

```
C:\web-scraping\                        true repo root
├── .git/, .gitignore
├── README.md                           setup + run instructions
├── docs/
│   ├── ARCHITECTURE.md                 this file
│   ├── OVERVIEW.md                     original design walkthrough (stale, Flow 1 era)
│   ├── STEP-1-RUNBOOK.md               original Flow 1 runbook (stale)
│   └── CALIBRATION-FINDINGS.md         selector-calibration notes
├── VAHAN_Demo_Script.docx              client-facing deliverables
├── VAHAN_Process_Overview.docx/.html
│
└── app/                                 ← everything the running app needs
    ├── backend/                         FastAPI API + scraper engine
    │   ├── __main__.py                  `python -m backend` entrypoint
    │   ├── main.py                      API routes; mounts ../frontend as static files
    │   ├── runner.py                    single background worker for dashboard-started runs
    │   ├── registry.py                  run state/logs; persists to runs/; sync_external_runs()
    │   ├── portal.py                    Playwright page-object for the VAHAN portal
    │   ├── captcha.py                   ddddocr wrapper (offline OCR, no external API)
    │   ├── validation.py                MB/BMW reconciliation parser
    │   ├── packaging.py                 zips report files + writes summary .xlsx
    │   ├── emailer.py                   sends the summary after every run (SMTP)
    │   ├── cloud_storage.py             optional Azure Blob Storage backup (no-op unless configured)
    │   ├── config.py                    config.yaml loader
    │   ├── settings.py                  env-driven server settings (host/port/CORS/SMTP)
    │   └── flows/
    │       ├── _resilience.py           shared self-heal retry helper
    │       └── flow1.py .. flow4.py     one file per PDD flow
    │
    ├── frontend/                        dashboard - plain files, no build step
    │   ├── index.html                   shell: React (CDN) + styles.css + app.js
    │   ├── styles.css                   white + sky-blue theme
    │   ├── app.js                       React app (React.createElement, no JSX/Babel)
    │   └── config.js / config.example.js
    │
    ├── tools/                           CLI entrypoints (no FastAPI needed)
    │   ├── calibrate.py                 dump live portal DOM → calibration/ (selector work)
    │   ├── run_flow.py                  headless CLI — what scheduled tasks actually run
    │   └── cleanup_output.py            retention cleanup for output/
    │
    ├── data/state_master.csv            36 States/UTs → short codes
    ├── config.yaml                      URL, filters, retry counts, selectors, maker buckets
    ├── requirements.txt, Dockerfile
    ├── .env / .env.example              secrets (git-ignored) / committed template
    ├── run-backend.cmd                  starts the one server
    ├── run-flow1-scheduled.cmd .. flow4 what each Windows Scheduled Task runs
    ├── cleanup-output.cmd               what the cleanup scheduled task runs
    │
    └── (git-ignored, created at runtime)
        ├── .venv/                       Python virtual environment
        ├── output/<run_id>_<flow>_<ts>/ per-run report files, zip, summary .xlsx
        ├── runs/                        persisted run registry (JSON per run)
        ├── logs/, downloads/, calibration/, scratch/
```

**Rule of thumb:** if the app needs it to run, it's under `app/`. Everything
outside `app/` is documentation or client deliverables, not code.

---

## 3. Request / run flow

1. **Dashboard start** — browser POSTs `/api/runs` → `registry.create_run(flow, params, source="dashboard")`
   → `runner.py` queues it on the single worker thread → the matching
   `flows/flowN.py` drives `portal.py` via Playwright.
2. **Scheduled/CLI start** — `run-flowN-scheduled.cmd` (launched by Windows
   Task Scheduler) runs `python -m tools.run_flow <flow>`, which calls
   `create_run(..., source="cli")` directly and runs in its own OS process,
   independent of the dashboard server.
3. **Per item** (state, RTO, or state+month combo depending on the flow):
   apply filters → solve captcha (OCR, up to 7 attempts — a documented
   deviation from the PDD's 5, after measuring real OCR accuracy) → download
   the report → rename to the PDD convention.
4. **Self-heal** — after the main pass, any failed items are retried in up to
   3 extra passes (fresh page reload each pass) before being logged as a
   final failure. This is the real lever used to push success rates toward
   100%, since a single pass can't guarantee it against probabilistic OCR.
5. **Packaging** — `packaging.py` zips the renamed files and writes a summary
   `.xlsx` (Execution Summary + Reconciliation sheets).
6. **Reconciliation** (PDD step 8.2) — `validation.py` sums MB/BMW
   registrations and compares against the All-India reference (Flow 1) or the
   relevant baseline flow (Flow 1→2/3, Flow 3→4); any mismatch is flagged but
   the summary is emailed either way.
7. **Delivery** — `emailer.py` sends the summary; `cloud_storage.py`
   best-effort backs up the zip/summary/run record to Azure Blob Storage if
   `AZURE_STORAGE_CONNECTION_STRING` is set (no-op otherwise).
8. **Dashboard visibility** — `registry.persist()` writes the run to
   `runs/<id>.json` after every state change; `sync_external_runs()` polls
   that folder so scheduled/CLI runs (a separate OS process) appear live in
   the dashboard without a backend restart, while never overwriting a
   dashboard-owned run's live in-memory state with a stale disk snapshot.

---

## 4. Scheduling

No in-app scheduler — Windows Task Scheduler owns timing, `tools/run_flow.py`
owns execution:

| Task | Trigger |
|---|---|
| `VahanScraper Flow1 Daily` | daily |
| `VahanScraper Flow2 Daily` | daily |
| `VahanScraper Flow3 Day5/Day15/Day25` | 3x/month (3 separate tasks — `schtasks` monthly triggers don't reliably take comma-separated days) |
| `VahanScraper Flow4 Day6/Day16/Day26` | 3x/month |
| `VahanScraper Output Cleanup` | retention cleanup |

Each task has `DisallowStartIfOnBatteries=False`, `StopIfGoingOnBatteries=False`,
`WakeToRun=True`, `StartWhenAvailable=True` (so it isn't blocked by battery
state, can wake a sleeping machine, and catches up a missed run), plus
**"Run whether user is logged on or not"** so it can fire while the machine is
locked. `/api/schedules` queries all of a flow's tasks live and surfaces the
soonest upcoming run in the dashboard.

---

## 5. Deployment

**Today:** runs locally on a Windows machine — `run-backend.cmd` for the
dashboard, Task Scheduler for unattended runs, as above.

**Planned (not yet deployed):** `app/Dockerfile` (Linux, Python 3.12-slim,
Playwright/Chromium system deps) packages the whole `app/` folder as one
container image for Azure App Service, with `cloud_storage.py` already wired
in so run output survives container restarts via Blob Storage instead of
local disk.

---

## 6. Key design decisions

| Decision | Why |
|---|---|
| Monolithic single process | Manager requirement; simplest deployable unit for App Service/DevOps — no CORS/proxy split to manage |
| Offline OCR (`ddddocr`), no LLM or external API | No API keys, no per-call cost, no external dependency for captcha solving |
| Captcha retries 5 → 7 | Documented deviation from the PDD after measuring real OCR accuracy; a pure retry-count bump has diminishing returns, so paired with self-heal (below) |
| Self-heal (3 extra passes over just the failures) | The actual mechanism that drives success rate toward 100% — single-pass retries alone can't guarantee it against probabilistic OCR |
| Navigation retries kept at 3, pause raised 3s → 10s | Stays PDD-compliant on retry *count* while giving more real recovery time against transient portal slowness |
| `source: "dashboard" | "cli"` on every run | Fixes a real bug where a backend restart wrongly marked still-alive scheduled/CLI runs as interrupted |
| `app/` wraps everything the app needs | One self-contained folder — zip it, Docker-build it, or deploy it as-is |
