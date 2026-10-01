# Step 1 Runbook — Install, Calibrate, Smoke-test

> **Status: DONE (2026-09-09).** The `.venv` was built, calibration was run
> against the live portal, real selectors are in `config.yaml`, and Flow 1
> passed 1-state / 3-state / reconciliation smoke tests. See
> [`CALIBRATION-FINDINGS.md`](CALIBRATION-FINDINGS.md). This doc is kept as the
> reproducible procedure and now ends with the **full 36-state run** (§1.4).

Goal of this step: get a working environment, replace the guessed selectors in
`config.yaml` with the **real** ones from the live VAHAN portal, and prove Flow 1
works.

---

## 1.1 Install

```powershell
cd C:\web-scraping
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

Sanity check:

```powershell
python -m py_compile backend\*.py backend\flows\*.py tools\*.py
python -c "import fastapi, playwright, ddddocr, openpyxl, yaml; print('deps OK')"
```

---

## 1.2 Calibrate the selectors

The portal is a PrimeFaces/JS app; its element ids are not in the PDD. This
script opens the real page and records every control.

```powershell
# visible browser, waits for you to press Enter before closing:
python -m tools.calibrate

# or headless (just dumps files, no window):
python -m tools.calibrate --headless --no-wait
```

Produces `calibration/`:

| File | Use |
|---|---|
| `page.png` | full-page screenshot — eyeball which control is which |
| `controls.json` | every visible `<select>`, `<input>`, `<button>`, PrimeFaces widget, `<img>` with `id` / `name` / `class` / nearby label |
| `page.html` | full DOM if you need to dig |

The script also **prints** the control list to the terminal.

### What to find and where to put it

Open `config.yaml` → `selectors:`. For each entry below, find the matching
control in `controls.json` / the screenshot and add a `css:` line (a real CSS
selector — prefer `#id`, else `[name='...']`, else a class). Keep the existing
`label:` / `kind:` lines as a fallback.

| Selector key | What it is on the page | Likely control type |
|---|---|---|
| `filter_panel_ready` | any element that only appears once filters are loaded (e.g. the "Report Filters" heading) | text/heading |
| `y_axis` | the "Y-Axis" dropdown | `<select>` or `.ui-selectonemenu` |
| `x_axis` | the "X-Axis" dropdown | same |
| `year_type` | "Year Type" dropdown (set to Calendar Year) | `<select>` / `.ui-selectonemenu` |
| `year_from` / `year_to` | the two year boxes ("2026 TO 2026") | `<input>` |
| `archived_flag` | multi-select checkbox menu with the 4 ACTIVE/ARCHIVE values | `.ui-selectcheckboxmenu` |
| `sub_category` | checkbox menu with vehicle sub-categories | `.ui-selectcheckboxmenu` |
| `state` | the "State" type-to-search select | filter select / `.ng-select` / `.ui-selectcheckboxmenu` |
| `rto` | the "RTO" select (needed for Flows 3–4) | same |
| `captcha_image` | the captcha `<img>` | `<img>` — note its `id` or `src` pattern |
| `captcha_refresh` | the little refresh icon next to the captcha | `<a>` / `<button>` |
| `captcha_input` | the "Enter CAPTCHA here" box | `<input>` |
| `apply_button` | the red **Apply** button | `<button>` / `<a>` |
| `report_grid` | the results table that appears after Apply | `.ui-datatable` / `<table>` |
| `download_excel` | **Download All Records Excel** button | `<button>` / `<a>` |
| `system_error` | the error toast/message area (used to detect a rejected captcha) | `.ui-messages-error` / `.alert` |

Example edit:

```yaml
  y_axis:
    css: "#yaxisVar"        # <-- add this, found in controls.json
    strategy: "label"
    label: "Y-Axis"
    kind: "select_one"
```

> Tip: while calibrating, also **manually run one report** in that browser
> (pick a state, solve the captcha, Apply, click *Download All Records Excel*)
> and open the downloaded file. Confirm the sheet has a **Maker** column and
> **month** columns — that's what `backend/validation.py` parses. If the month
> headers look different (e.g. `2026-01` instead of `JAN`), tell me and I'll
> adjust `_month_key`.

---

## 1.3 Smoke-test Flow 1 (one state)

Set `browser.headless: false` and `browser.slow_mo: 300` in `config.yaml` so you
can watch it, then:

```powershell
python -m tools.run_flow flow1_state_monthwise --states Goa --skip-all-india --skip-reconciliation
```

Watch the browser drive the filters, pause for the captcha (type it in the
terminal is **not** wired for the CLI — use the dashboard for interactive
captcha, see below), Apply, and download.

**Interactive version (recommended for the first try):**

```powershell
uvicorn backend.main:app --port 8000
```

Open <http://localhost:8000> → Start run → Flow = Flow 1 → *Limit to states* =
`Goa` → tick both skip boxes → **Start run**. When the captcha panel appears,
type the characters and Submit.

### Expected result

```
output/<run_id>_flow1_<ts>/
├── files/Monthwise_2026-GA.xlsx     ← exists, > 0 bytes
└── Flow1_StateMonthwise_2026_<ts>.zip
```

Run state = `done`, 1 succeeded, 0 failed.

### If something breaks

| Symptom | Fix |
|---|---|
| `Website not opening after 3 attempts` | portal slow/down — raise `portal.load_timeout`, retry |
| Hangs on a filter step, log says `... skipped: ...` | that selector is wrong — recheck `controls.json`, set an explicit `css:` |
| Captcha loop never succeeds | check `captcha_image` / `captcha_input` / `apply_button` selectors; verify `system_error` matches the real error element |
| Download step times out | `download_excel` selector wrong, or the grid needs longer — raise `portal.report_timeout` |
| `No 'Maker' header row found` during reconciliation | export layout differs — send me the file, adjust `validation.py` |

---

## 1.4 Full Flow 1 run

Once one state works end to end:

```powershell
# dashboard, all 36 states + All-India + reconciliation:
uvicorn backend.main:app --port 8000     # leave "Limit to states" blank, untick skips
```

Check `Flow1_Summary_<ts>.xlsx` → **Reconciliation** sheet: every row should
have `Match = YES`.

---

## 1.5 Then

- Report back which selectors you filled in (paste your `selectors:` block) and
  whether the smoke test passed.
- Next we build **Flow 2** (adds the "1 Month Flexible" calendar loop) on the
  same engine, then Flows 3–4 (RTO loop), then wire the **email** step with your
  SMTP details.
