# VAHAN Registration Data Scraper

Automation of the RPA Process Definition Document *"Vahan Registration Data for
Market Intelligence"*. Pulls vehicle-registration reports from the
[VAHAN Public Report portal](https://analytics.parivahan.gov.in/analytics/vahanpublicreport?lang=en),
renames each export to the PDD convention, zips them, runs the MB / BMW
reconciliation, and emails the summary - unattended, on a schedule.

**Stack:** Python + Playwright (Chromium) + FastAPI + a buildless React dashboard,
packaged as one monolithic application under [`app/`](app/).
**Captcha:** offline OCR (`ddddocr`), automatic retries, self-heals failed
items with extra passes - no manual captcha entry needed for scheduled runs.

| Flow | Status | Schedule |
|------|--------|----------|
| Flow 1 - `Vahan_StateMonthwise` (36 states, Maker x Month-wise) | ✅ | Daily |
| Flow 2 - `Vahan_StateFuelwise` (36 states x every month, Maker x Fuel) | ✅ | Daily |
| Flow 3 - `Vahan_RTO-Monthwise` (~1,700 RTOs, Maker x Month-wise) | ✅ | 3x/month |
| Flow 4 - `Vahan_RTOFuelwise` (~1,700 RTOs x every month, Maker x Fuel) | ✅ | 3x/month |

---

## Project layout

```
app/                     everything the application needs - one folder
├── backend/             FastAPI API + the scraper engine
│   ├── main.py          API routes; also serves ../frontend as static files
│   ├── runner.py        single background worker (one dashboard-started run at a time)
│   ├── registry.py      run state/logs, persisted to runs/, picks up scheduled runs too
│   ├── portal.py        Playwright page-object for the VAHAN portal
│   ├── captcha.py       ddddocr wrapper
│   ├── validation.py    MB/BMW reconciliation parser
│   ├── packaging.py     zip + summary .xlsx
│   ├── emailer.py       sends the summary after every run
│   ├── cloud_storage.py optional Azure Blob Storage backup (no-op unless configured)
│   ├── config.py        config.yaml loader
│   └── flows/           flow1.py .. flow4.py - one file per PDD flow
├── frontend/             the dashboard - plain files, no build step
│   ├── index.html        shell: loads React (CDN) + styles.css + app.js
│   ├── styles.css        white + sky-blue theme
│   └── app.js             React app (React.createElement, no JSX/Babel)
├── tools/
│   ├── calibrate.py       dump live portal DOM to calibration/ (selector work)
│   ├── run_flow.py        headless CLI - what the scheduled tasks actually run
│   └── cleanup_output.py  retention cleanup for output/
├── config.yaml            URL, filters, retry counts, selectors, maker buckets
├── data/state_master.csv  36 States/UTs -> short codes
├── requirements.txt, Dockerfile, .env.example
├── run-backend.cmd                  starts the one server
├── run-flow1-scheduled.cmd ... 4    what each scheduled task runs
└── cleanup-output.cmd                the retention-cleanup scheduled task runs this

docs/                     project documentation (not application code)
README.md, *.docx/html    this file + client-facing deliverables
```

Everything the running application touches - code, config, and its own
`output/`, `runs/`, `logs/` working folders - lives under `app/`. Nothing
outside `app/` is required to run it; that's deliberate, so the whole folder
is one self-contained unit to deploy (Docker build context, zip to copy to
another machine, etc.).

---

## Setup

```powershell
cd C:\web-scraping\app
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
```

## 1. Calibrate selectors (only if the portal's DOM changes)

```powershell
cd app
.\.venv\Scripts\python -m tools.calibrate
```

Outputs to `calibration/`: `page.png`, `page.html`, `controls.json`. Match each
filter row to a control and paste concrete CSS into `config.yaml -> selectors`.
Already calibrated against the live site - only needed if the portal changes.

## 2. Configure

```powershell
copy .env.example .env
```

| Var | Default | Meaning |
|---|---|---|
| `VAHAN_HOST` / `VAHAN_PORT` | `127.0.0.1` / `8000` | where the one server binds |
| `VAHAN_RELOAD` | `false` | uvicorn auto-reload (dev only) |
| `VAHAN_CORS_ORIGINS` | `*` | allowed browser origins |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_TO` / `SMTP_STARTTLS` | *(empty)* | email delivery - every flow emails its summary automatically when these are filled in; leave blank to skip email |
| `VAHAN_HEADLESS`, `VAHAN_SLOW_MO`, `VAHAN_PORTAL_URL`, `VAHAN_CAPTCHA_MANUAL_FALLBACK`, `VAHAN_OUTPUT_DIR` | *(from `config.yaml`)* | optional overrides of `config.yaml` |

`.env` is git-ignored (real secrets); `.env.example` is the committed template.
Portal behaviour, filters, retry counts and selectors stay in `config.yaml`
(committed - no secrets there).

## 3. Run it

One process serves both the JSON API (`/api/*`) and the dashboard (everything
else) on the same port - monolithic, no separate frontend server:

```powershell
run-backend.cmd
```

Open <http://127.0.0.1:8000>.

- **Start a run** - pick a flow. Leave *States* blank for all 36, or pick
  specific ones for a quick test.
- **Scheduled runs panel** - shows each flow's Windows Task Scheduler status
  live (next run time, last result). Click a flow to filter the Runs list
  down to just that flow's history.
- **Captcha** - fully automatic (OCR + retries + self-heal passes). A manual
  fallback only appears if OCR is very unsure and `VAHAN_CAPTCHA_MANUAL_FALLBACK=true`.
- **Output** - when done, download the zip and the summary `.xlsx` from the
  dashboard, or find them under `output/<run_id>_<flow>_<timestamp>/`.

Editing a `frontend/` file just needs a browser refresh - no build step.

## 4. Headless / scheduled runs

```powershell
.\.venv\Scripts\python -m tools.run_flow flow1_state_monthwise
.\.venv\Scripts\python -m tools.run_flow flow2_state_fuelwise --states GA --months SEP
```

This is exactly what the `run-flow*-scheduled.cmd` scripts call, which is what
the Windows Scheduled Tasks run (`VahanScraper Flow1 Daily`, `Flow2 Daily`,
`Flow3 Day5/15/25`, `Flow4 Day6/16/26`, `Output Cleanup`). Each task's
"Run whether user is logged on or not" security option must be enabled for it
to fire when the machine is locked - do this once via Task Scheduler GUI
(Properties -> General), since it needs a Windows password Task Scheduler
can't be given over the command line.

With pure OCR and no operator, a captcha-heavy item can still fail after all
retries + self-heal passes - it's logged and skipped, listed in the summary
email, and can be backfilled with a small targeted re-run
(`--states X --months Y`) without redoing the whole flow.

---

## Output layout

```
output/<run_id>_<flow>_<ts>/
├── files/                        the report files that get zipped
├── all_india/                    Flow 1 only - reference file, NOT zipped
├── Flow{N}_<FlowName>_<ts>.zip
└── Flow{N}_Summary_<ts>.xlsx      Execution Summary + Reconciliation sheets
```

## Reconciliation (PDD Step 8.2)

For each of MB (`MERCEDES BENZ`, `MERCEDES BENZ - AG`,
`MERCEDES BENZ INDIA PVT LTD`, `DAIMLER AG`) and BMW (`BMW INDIA PVT LTD`):

- **Flow 1** sums per-month registrations across all 36 state files and
  compares to the All-India reference file, month by month.
- **Flows 2/3/4** sum their own totals and compare against the relevant
  baseline flow (Flow 1 for Flow 2/3; Flow 3 for Flow 4), auto-discovering the
  most recent *complete* baseline run.

Any delta != 0 flags the run as *MISMATCH* in the summary - sent by email
either way, pass or mismatch, per the PDD.

## Config reference - `config.yaml`

- `portal.*` - URL, timeouts, retry counts (navigation 3 per PDD; captcha 7 -
  a deliberate, documented increase from the PDD's "5" after measuring real
  OCR accuracy live).
- `browser.headless` - set `false` while calibrating.
- `filters.*` - the constant filter values (archived flag, year type, sub-category).
- `reconciliation.buckets` - the MB / BMW maker name lists.
- `selectors.*` - per-control locators (`css:` override or label strategy).
