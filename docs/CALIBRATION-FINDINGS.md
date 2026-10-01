# Calibration Findings (live portal, 2026-09-09)

What `tools/calibrate.py` + a live Flow 1 smoke test revealed about the real
VAHAN portal, and how the code was adjusted. Raw dumps are in `calibration/`
(`page.png`, `page.html`, `controls.json`).

## The portal is a Spring MVC form, not PrimeFaces

- `#vahanPublicForm` posts to the same URL. **Apply (`#applyTrigger`) is a full
  HTTP POST** that re-renders the whole page; the report grid comes back inline.
- Filter selections **persist server-side** across the POST, so the per-state
  loop only needs to change the State and re-solve the captcha.

## Real selectors (now in `config.yaml`)

| Purpose | Selector | Type |
|---|---|---|
| Y-Axis | `#yAxis` | native `<select>`; "Maker" = value `vehicleMakerName` |
| X-Axis | `#xAxis` | native `<select>`; "Month Wise" = value `monthWise`; options regenerate from Y-Axis |
| Year Type | `#reportType` | `0`=CALENDAR YEAR (default), `1`=FINANCIAL YEAR, `4`=Last 1 Year, `9`=1 Month Flexible |
| Year range | `#fromYear` / `#toYear` | text inputs; become `readonly` after a POST -> we set them via JS |
| Archived Flag | `#archivedFlags` | hidden native multi-`<select>` (all 4 selected by default) |
| Sub-Category | `#vehicleSubCategory` | hidden native multi-`<select>`; values = visible text |
| State | `#stateName` | hidden native multi-`<select>`; **option value = state code already** (`GA`, `MH`, …) |
| RTO | `#rtoCode` | hidden native multi-`<select>` (Flows 3–4) |
| Captcha image / refresh / input / error | `#captchaImage` / `#captchaImg` / `#externalCaptcha` / `#captchaError` | image src = `/analytics/captcha-gen` |
| Apply | `#applyTrigger` | submit button |

### Multi-selects: drive the native `<select>`, not the widget

Each is wrapped by the `multiselect-dropdown` JS widget (a rendered
`.multiselect-dropdown` div with a search box + checkbox rows). We ignore the
widget and set `.selected` on the underlying `<option>`s via `page.evaluate`
+ dispatch `input`/`change` — the form reads the native select on submit, and
the page's own listeners (RTO-enable, dynamic header) react to the `change`.

## The Maker view is special

When **Y-Axis = Maker**, the result is not the generic grid. It renders:

- header `#makerDynamicReportHeader`
- a **server-paginated** table `#makerPagedReportTable` ("Page 1 of 2", …)
- its own export buttons: **`#downloadMakerAllExcel`** ("Download All Records
  Excel") and `#downloadMakerAllCsv`

`#downloadMakerAllExcel` runs `exportAllMakerRecords('xlsx')`: it loops
`fetch('/analytics/vahanpublicreport/maker-report-page')` over every page,
assembles the rows client-side, then `XLSX.writeFile(...)` triggers the browser
download. Consequences:

- it can take 10–60 s and shows progress in `#makerExportStatus`
  ("Fetching batch N…");
- the first click sometimes doesn't register (a prior export's
  `exportInProgress` guard) — `download_excel()` now retries the click up to 3×
  and treats "No record found" / "limit exceeded" status as a hard failure.

Non-Maker Y-Axis values use the generic `#downloadBtn1`; `config.yaml` lists
both so the code works for future flows.

## Exported file layout (what `validation.py` parses)

```
row 0:  <heading>            (merged)
row 1:  (blank)
row 2:  Maker | 2026-Jan | 2026-Feb | … | 2026-Sep | Total
row 3+: <maker> | n | n | … | n
foot:   Page Total | …
```

`parse_maker_month` finds the row whose first cell is `Maker`, treats any header
cell containing a month name as a month column, and stops at blank makers. Month
headers are `2026-Jan` style — handled.

## State master corrected

Portal `#stateName` codes differ from first assumptions: **Odisha = `OR`**
(not OD), **"UT of DNH and DD" = `DD`**. `data/state_master.csv` now matches the
portal's 36 entries exactly. The code reads names+codes live from `#stateName`
anyway; the CSV is a fallback / reference.

## Maker buckets — real strings

The portal uses `MERCEDES -BENZ AG`, `MERCEDES-BENZ INDIA PVT LTD`,
`MERCEDES BENZ`, `BMW INDIA PVT LTD`. `DAIMLER AG` is not present in the
LMV/LPV 2026 data (kept in the bucket for when it appears).
`DAIMLER INDIA COMMERCIAL VEHICLES PVT. LTD` is **excluded** (commercial
trucks). `validation._norm_maker` normalises case / spacing / dash so the PDD
spellings and the portal strings both match.

## Smoke-test results

| Test | Result |
|---|---|
| 1 state (GA), OCR-only | ✅ captcha solved attempt 1, file downloaded, valid (39 makers) |
| 3 states (AN, CH, GA) loop | ✅ 3/3, one export needed the auto-retry |
| 2 states + All-India + reconciliation | ✅ ran end to end; MISMATCH flagged correctly (only 2 of 36 states) |

`ddddocr` solved the captcha on the first attempt in every run here. Full 36-state
runs will still hit occasional misreads — the 5-retry loop + optional dashboard
manual fallback cover that.

## Dashboard-specific fixes (found while testing the UI)

1. **`NotImplementedError` launching the browser from the dashboard.** uvicorn
   installs `WindowsSelectorEventLoopPolicy`; Playwright's sync API, run from the
   background worker thread, then can't spawn the browser subprocess. Fix:
   `runner._prepare_event_loop()` forces `WindowsProactorEventLoopPolicy` for the
   worker thread. (The CLI was unaffected — it runs on the main thread.)
2. **`all` in the States box produced an empty zip.** It was treated as a literal
   state name. Now `all` / `*` / blank = run all 36; a filter that matches nothing
   raises with the list of valid codes; name matching is code / exact-name /
   prefix / whole-word (so `GA` no longer also matches "na**ga**land",
   "chandi**ga**rh", …).
3. **Maker export's first click was often ignored** (its JS bails while the view's
   own auto-pagination is still `loading`), causing a 120 s stall then a retry.
   `download_excel()` now waits for the maker table to settle, then re-clicks
   within the same attempt if `#makerExportStatus` stays blank → downloads on the
   first try.
