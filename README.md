# VAHAN Registration Data Scraper

Automation of the RPA Process Definition Document *"Vahan Registration Data for
Market Intelligence"*. Pulls vehicle-registration reports from the
[VAHAN Public Report portal](https://analytics.parivahan.gov.in/analytics/vahanpublicreport?lang=en),
renames each export to the PDD convention, zips them, and runs the MB / BMW
reconciliation.

**Stack:** Python + Playwright (Chromium) + FastAPI dashboard.
**Captcha:** auto OCR (`ddddocr`) with a manual fallback surfaced in the dashboard.

📖 **New here? Read [`docs/OVERVIEW.md`](docs/OVERVIEW.md)** — what was built, where, and why.
▶️ **Doing the next step? Follow [`docs/STEP-1-RUNBOOK.md`](docs/STEP-1-RUNBOOK.md).**

| Flow | Status |
|------|--------|
| Flow 1 – `Vahan_StateMonthwise` (36 states, Maker × Month-wise) | ✅ implemented |
| Flow 2 – `Vahan_StateFuelwise` | ⏳ next (engine ready) |
| Flow 3 – `Vahan_RTO-Monthwise` | ⏳ |
| Flow 4 – `Vahan_RTOFuelwise` | ⏳ |

---

## Setup

```powershell
cd C:\web-scraping
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## 1. Calibrate selectors (once, before the first real run)

The PDD only has screenshots, not DOM ids. This opens the live site in a visible
browser and dumps every control:

```powershell
python -m tools.calibrate
```

Outputs to `calibration/`: `page.png`, `page.html`, `controls.json`, plus a
printed list. Match each filter row (Y-Axis, X-Axis, Year Type, Archived,
Sub-Category, State, captcha image / input, Apply, Download All Records Excel)
to a control and paste concrete CSS into `config.yaml → selectors` as
`css: "..."`. The label-based defaults may already work for some rows.

## 2. Configure (`.env` + `frontend/config.js`)

**Backend** reads `.env` at the project root (git-ignored). Copy the template:

```powershell
copy .env.example .env
```

| Var | Default | Meaning |
|---|---|---|
| `VAHAN_HOST` / `VAHAN_PORT` | `127.0.0.1` / `8000` | where `python -m backend` binds |
| `VAHAN_RELOAD` | `false` | uvicorn auto-reload |
| `VAHAN_CORS_ORIGINS` | `*` | allowed browser origins (set explicit ones if the frontend is hosted separately) |
| `VAHAN_HEADLESS`, `VAHAN_SLOW_MO`, `VAHAN_PORTAL_URL`, `VAHAN_CAPTCHA_MANUAL_FALLBACK`, `VAHAN_OUTPUT_DIR` | *(from `config.yaml`)* | optional overrides of `config.yaml` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_TO` / `SMTP_STARTTLS` | *(empty)* | placeholders for the email step (not wired yet) |

Portal behaviour, filters and selectors stay in **`config.yaml`** (committed).

**Frontend** has no build step, so it can't read `.env`. Its equivalent is
[`frontend/config.js`](frontend/config.js) — plain JS, edited in place:

```js
window.__VAHAN_CONFIG__ = { API_BASE: "" };   // "" = same origin
```

## 3. Run it

### A) One server (simplest) — backend also serves the dashboard

`.env`: `VAHAN_SERVE_FRONTEND=true`

```powershell
python -m backend
```

Open <http://localhost:8000>. API under `/api/*`, dashboard at `/`.

### B) Two servers — backend and frontend run separately

`.env`:
```
VAHAN_SERVE_FRONTEND=false
FRONTEND_PORT=5173
VAHAN_API_BASE=http://localhost:8000
```

```powershell
# terminal 1 - API only, port 8000
run-backend.cmd            # (or:  .\.venv\Scripts\python.exe -m backend)

# terminal 2 - dashboard, port 5173
run-frontend.cmd           # (or:  .\.venv\Scripts\python.exe -m tools.serve_frontend)
```

The two `.cmd` scripts just call the venv's Python, so they work from a plain
`cmd`/PowerShell window with no venv activation.

Open <http://localhost:5173>. The frontend server generates `/config.js` from
`VAHAN_API_BASE`, so the dashboard calls the backend on :8000 cross-origin
(CORS is handled by `VAHAN_CORS_ORIGINS`, `*` by default). `python -m backend`
alone then answers only JSON; `GET /` returns a small service blurb.

Either way: editing a `frontend/` file just needs a browser refresh — no build
step. `--reload` on Windows can orphan the child process; prefer restarting by
hand.

- **Start a run** – pick Flow 1. Leave *Limit to states* blank for all 36, or
  enter e.g. `Goa` for a quick smoke test.
- **Captcha** – if OCR is unsure, the run pauses and shows the captcha image;
  type it and hit Submit. It resumes automatically.
- **Output** – when done, download the zip and the summary `.xlsx`. Files also
  live under `output/<run_id>_flow1_<timestamp>/`.

## 4. Headless / scheduled run (PDD: daily + ad hoc)

```powershell
python -m tools.run_flow flow1_state_monthwise
python -m tools.run_flow flow1_state_monthwise --states Goa,Chandigarh --skip-all-india
```

Wire this into Windows Task Scheduler for the daily cadence. Note: with pure OCR
and no operator, captcha-heavy runs may exhaust retries on some items — those are
logged and skipped, and listed in the summary.

---

## Output layout

```
output/<run_id>_flow1_<ts>/
├── files/                       Monthwise_2026-MH.xlsx, ...   (36, these get zipped)
├── all_india/                   Monthwise_2026-ALL-INDIA.xlsx (reference, NOT zipped)
├── Flow1_StateMonthwise_2026_<ts>.zip
└── Flow1_Summary_<ts>.xlsx      Execution Summary + Reconciliation sheets
```

## Reconciliation (PDD Step 8.2)

For each of MB (`MERCEDES BENZ`, `MERCEDES BENZ - AG`,
`MERCEDES BENZ INDIA PVT LTD`, `DAIMLER AG`) and BMW (`BMW INDIA PVT LTD`):
sum the per-month registrations across all 36 state files and compare to the
All-India file month by month. Any delta ≠ 0 flags the run as *MISMATCH* in the
summary. Email delivery of that summary is stubbed for now (files are produced;
sending is the next wiring step).

## Config reference — `config.yaml`

- `portal.*` – URL, timeouts, retry counts (navigation 3 / captcha 5 / report 2, per the PDD).
- `browser.headless` – set `false` while calibrating.
- `filters.*` – the constant filter values (archived flag, year type, sub-category).
- `reconciliation.buckets` – the MB / BMW maker name lists.
- `selectors.*` – per-control locators (`css:` override or label strategy).

## Project layout

Frontend and backend are separate folders; one server hosts both.

```
backend/                FastAPI API + the scraper engine
├── main.py             API routes + serves ../frontend as static files
├── runner.py           single background worker (one run at a time)
├── registry.py         run state, logs, manual-captcha queue
├── portal.py           Playwright page-object for the VAHAN portal
├── captcha.py          ddddocr wrapper
├── validation.py       MB/BMW reconciliation parser
├── packaging.py        zip + summary .xlsx
├── config.py           config.yaml loader + state-code mapping
└── flows/flow1.py      Flow 1 orchestration

frontend/               the dashboard — plain files, no build step
├── index.html          shell: loads React (CDN) + styles.css + app.js
├── styles.css          white + sky-blue theme
└── app.js              React app (React.createElement, no JSX/Babel)

tools/
├── calibrate.py        dump live DOM to calibration/
└── run_flow.py         headless CLI for schedulers

config.yaml             URL, filters, retry counts, selectors, buckets
data/state_master.csv   36 States/UTs → short codes
```

### Running frontend + backend fully separately (optional)

They already are separate folders. If you also want two processes:

```powershell
uvicorn backend.main:app --port 8000          # API only is fine; CORS is open
python -m http.server 5173 --directory frontend
```

Then set `API_BASE = "http://localhost:8000"` at the top of `frontend/app.js`
and open <http://localhost:5173>.
