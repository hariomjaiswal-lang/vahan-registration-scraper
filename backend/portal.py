"""Playwright page-object for the VAHAN Public Report portal.

Calibrated against the live site (Sep 2026). The portal is a Spring MVC form:

* single selects (Y-Axis `#yAxis`, X-Axis `#xAxis`, Year Type `#reportType`) are
  plain <select> elements;
* multi-selects (State `#stateName`, Sub-Category `#vehicleSubCategory`,
  Archived Flag `#archivedFlags`, RTO `#rtoCode`) are hidden native <select>
  elements wrapped by the `multiselect-dropdown` widget - we drive the native
  <select> directly (set `.selected`, dispatch `change`), which the form reads
  on submit;
* **Apply (`#applyTrigger`) is a full-page POST** - the report grid and the
  `#downloadBtn1` ("Download All Records Excel") button come back on the
  re-rendered page. Filter selections persist server-side across the POST.

All selectors come from `config.yaml -> selectors`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from playwright.sync_api import (
    Download,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from io import BytesIO

from PIL import Image

from backend import captcha as captcha_ocr
from backend import registry


def _looks_blank(png_bytes: bytes) -> bool:
    """True if the captcha screenshot is (near) blank - a uniform white/grey
    square with no digits painted on it yet. Caught by comparing the darkest
    and lightest pixel: real captcha text has sharp contrast; an unloaded
    image is flat."""
    try:
        with Image.open(BytesIO(png_bytes)) as im:
            lo, hi = im.convert("L").getextrema()
        return (hi - lo) < 25
    except Exception:
        return False


class PortalError(RuntimeError):
    pass


class CaptchaExhausted(PortalError):
    pass


class VahanPortal:
    def __init__(self, cfg: dict, run: registry.Run | None = None):
        self.cfg = cfg
        self.run = run
        self.sel = cfg["selectors"]
        self.axes = cfg["axes"]
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None

    def _log(self, msg: str, level: str = "info") -> None:
        if self.run:
            self.run.log(msg, level)

    # --------------------------------------------------------------- lifecycle
    def __enter__(self) -> "VahanPortal":
        b = self.cfg["browser"]
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=b.get("headless", True), slow_mo=b.get("slow_mo", 0)
        )
        self._context = self._browser.new_context(accept_downloads=True)
        self._context.set_default_timeout(30_000)
        self.page = self._context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        for closer in (self._context, self._browser):
            try:
                closer and closer.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass

    # ---------------------------------------------------------------- navigation
    def open_report_page(self) -> None:
        url = self.cfg["portal"]["url"]
        retries = self.cfg["portal"]["navigation_retries"]
        load_to = self.cfg["portal"]["load_timeout"] * 1000
        last = None
        for attempt in range(1, retries + 1):
            try:
                self._log(f"Navigating to portal (attempt {attempt}/{retries})")
                self.page.goto(url, wait_until="domcontentloaded", timeout=load_to)
                self.page.wait_for_selector(self.sel["filter_panel_ready"], timeout=load_to)
                self.page.wait_for_timeout(1500)  # let filter scripts wire up
                self._log("Report filter form is ready")
                return
            except PWTimeout as e:  # noqa: PERF203
                last = e
                self._log(f"Portal did not load: {e}", "warn")
                # PDD Business Exceptions: "Website Not opening -> Retry 3
                # times" - the count stays at 3 (navigation_retries), but a
                # longer pause between attempts gives real, observed
                # transient portal blips (confirmed live: a failed load
                # recovered to <1s within seconds) more room to clear before
                # the next attempt, instead of retrying into the same stall.
                time.sleep(10)
        raise PortalError(f"Website not opening after {retries} attempts: {last}")

    # ---------------------------------------------------------- native <select>
    def _select_one(self, css: str, value: str) -> None:
        """Set a native <select> by value via JS (tolerates hidden/quirky
        widgets) and fire input+change."""
        self.page.eval_on_selector(
            css,
            """(el, v) => {
                if (![...el.options].some(o => o.value === v)) el.add(new Option(v, v));
                el.value = v;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            }""",
            value,
        )

    def _set_input(self, css: str, value: str) -> None:
        """Set an <input> value via JS - works even when a datepicker has made
        the field readonly after a POST re-render."""
        self.page.eval_on_selector(
            css,
            """(el, v) => {
                el.removeAttribute('readonly');
                el.value = v;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            }""",
            value,
        )

    def set_multi(self, css: str, values: list[str]) -> None:
        """Select exactly `values` on a (possibly hidden) native multi <select>
        and fire change so the page's listeners react."""
        self.page.eval_on_selector(
            css,
            """(el, wanted) => {
                const set = new Set(wanted);
                let hit = 0;
                for (const o of el.options) {
                    o.selected = set.has(o.value) || set.has(o.text.trim());
                    if (o.selected) hit++;
                }
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                if (typeof el.loadOptions === 'function') el.loadOptions();
                return hit;
            }""",
            values,
        )

    def clear_multi(self, css: str) -> None:
        self.set_multi(css, [])

    def state_options(self) -> list[dict]:
        """[{code, name}] straight from the #stateName <select>."""
        return self.page.eval_on_selector(
            self.sel["state_select"],
            "el => [...el.options].map(o => ({code:o.value, name:o.text.trim()}))"
            ".filter(o => o.code && !o.name.startsWith('---'))",
        )

    # -------------------------------------------------------------- common setup
    def apply_common_filters(self, calendar_year: bool = True) -> None:
        """Archived Flag = all, Sub-Category = LMV + LPV (every flow).
        When `calendar_year` (Flow 1) also set Year Type = Calendar Year for the
        current year; Flow 2/4 set their own '1 Month Flexible' period instead."""
        f = self.cfg["filters"]
        self._log("Applying common filters")
        self.set_multi(self.sel["archived_flag_select"], f["archived_flag"])
        self.set_multi(self.sel["sub_category_select"], f["sub_category"])
        if calendar_year:
            self._select_one(self.sel["year_type"], f["year_type_value"])
            year = str(time.localtime().tm_year)
            self._set_input(self.sel["year_from"], year)
            self._set_input(self.sel["year_to"], year)
            self._log(f"  archived=all, year_type={f['year_type_value']}, "
                      f"years={year}, sub_category={f['sub_category']}")
        else:
            self._log(f"  archived=all, sub_category={f['sub_category']} "
                      "(period set per-month)")

    def set_axes(self, y_value: str, x_value: str) -> None:
        """Set Y-Axis then X-Axis. X-Axis options are (re)generated from the
        Y-Axis value, so trigger that first."""
        self._select_one(self.sel["y_axis"], y_value)
        # The portal repopulates #xAxis on a 'click' of #yAxis; nudge every path.
        self.page.dispatch_event(self.sel["y_axis"], "click")
        self.page.wait_for_timeout(400)
        self._select_one(self.sel["x_axis"], x_value)
        self.page.eval_on_selector_all(
            "#yAxis_hidden, #xAxis_hidden",
            """els => {
                const y = document.getElementById('yAxis');
                const x = document.getElementById('xAxis');
                if (document.getElementById('yAxis_hidden')) document.getElementById('yAxis_hidden').value = y.value;
                if (document.getElementById('xAxis_hidden')) document.getElementById('xAxis_hidden').value = x.value;
            }""",
        )
        self._log(f"Axes set: Y={y_value}, X={x_value}")

    def set_axes_maker_monthwise(self) -> None:
        self.set_axes(self.axes["maker_value"], self.axes["monthwise_value"])

    def set_axes_maker_fuel(self) -> None:
        """Flow 2/4: Y-Axis = Maker, X-Axis = Fuel (renders the maker view)."""
        self.set_axes(self.axes["maker_value"], self.axes["fuel_value"])

    def set_month_period(self, year: int, month: int) -> None:
        """Year Type = '1 Month Flexible' (#reportType=9). The current portal
        drives the date range from the #reportYear / #reportMonth selects (month
        is 0-based); its own 'change' handler calls setMonthRange() which fills
        the .dpd3/.dpd4 datepickers (from = 1st; to = last day of month, or
        yesterday for the current month)."""
        mf = self.cfg["filters"]["month_flexible_value"]
        self.page.evaluate(
            """(a) => {
                const $ = window.jQuery;
                $('#reportType').val(a.mf).trigger('change');
                $('#reportYear').val(String(a.year));
                $('#reportMonth').val(String(a.m0)).trigger('change');
            }""",
            {"mf": mf, "year": year, "m0": month - 1},
        )
        self.page.wait_for_timeout(700)
        try:
            frm = self.page.input_value(self.sel["month_from_date"])
            to = self.page.input_value(self.sel["month_to_date"])
        except Exception:
            frm = to = "?"
        self._log(f"Period: {frm} -> {to}  (1 Month Flexible, {year}-{month:02d})")

    def select_only_state(self, code: str) -> None:
        self.set_multi(self.sel["state_select"], [code])
        self.page.wait_for_timeout(300)  # RTO / axis listeners settle

    def rto_options(self, retries: int = 3) -> list[dict]:
        """[{value, code, name}] from #rtoCode - populated once a single State
        is selected. `value` is the bare numeric option value the form needs;
        `code` is the short RTO code parsed off the option text
        ('MAPUSA RTO - GA3' -> 'GA3'), used for the file name."""
        for attempt in range(retries):
            raw = self.page.eval_on_selector(
                self.sel["rto_select"],
                "el => [...el.options].map(o => ({value:o.value, text:o.text.trim()}))"
                ".filter(o => o.value && !/^-+ ?select/i.test(o.text))",
            )
            if raw:
                break
            self.page.wait_for_timeout(500)
        else:
            raw = []
        out = []
        for r in raw:
            text = r["text"]
            code = text.rsplit(" - ", 1)[-1].strip() if " - " in text else text
            out.append({"value": r["value"], "code": code, "name": text})
        return out

    def select_only_rto(self, value: str) -> None:
        self.set_multi(self.sel["rto_select"], [value])
        self.page.wait_for_timeout(200)

    # -------------------------------------------------------------- captcha+apply
    def _captcha_bytes(self) -> bytes:
        img = self.page.locator(self.sel["captcha_image"]).first
        img.wait_for(state="visible", timeout=15_000)
        # "visible" only means the <img> has a layout box - the browser can
        # still be mid-download of the actual pixels, which occasionally
        # produced a blank white screenshot. Wait for the image to actually
        # finish loading, then verify the screenshot really has something
        # painted on it (not just "loaded" per the browser but still blank)
        # before handing it to OCR/the operator - retrying a few times if not.
        shot = b""
        for attempt in range(4):
            try:
                img.evaluate(
                    "el => new Promise(resolve => {"
                    "  if (el.complete && el.naturalWidth > 0) return resolve();"
                    "  el.addEventListener('load', resolve, {once: true});"
                    "  el.addEventListener('error', resolve, {once: true});"
                    "  setTimeout(resolve, 5000);"
                    "})"
                )
            except Exception:
                pass
            shot = img.screenshot()
            if not _looks_blank(shot):
                return shot
            self._log(f"Captcha image still blank on capture attempt {attempt + 1}/4; waiting and retrying", "warn")
            self.page.wait_for_timeout(400 * (attempt + 1))
        return shot  # out of retries - hand over what we have; the caller's own retry loop takes it from here

    def _refresh_captcha(self) -> None:
        try:
            self.page.locator(self.sel["captcha_refresh"]).first.click()
            self.page.wait_for_timeout(800)
        except Exception:
            self._log("Could not click captcha refresh", "warn")

    def _reassert_filters_present(self) -> bool:
        return self.page.locator(self.sel["captcha_input"]).count() > 0

    def solve_captcha_and_apply(self, reassert=None) -> None:
        """PDD Stage 4/5. OCR the captcha (operator fallback via dashboard),
        submit the form (full-page POST), confirm the report rendered.
        `reassert` is an optional callable re-applying filters before each retry
        (selections normally persist server-side, but a rejected captcha render
        occasionally drops the loop item)."""
        retries = self.cfg["portal"]["captcha_retries"]
        nav_to = self.cfg["portal"]["report_timeout"] * 1000
        cap_cfg = self.cfg.get("captcha", {})
        manual = cap_cfg.get("manual_fallback", True)
        op_to = cap_cfg.get("operator_timeout", 600)
        for attempt in range(1, retries + 1):
            try:
                img = self._captcha_bytes()
                guess = captcha_ocr.recognise(img)
                value = guess
                if self.run and manual and not captcha_ocr.looks_plausible(guess):
                    self._log(f"OCR unsure ('{guess}') - asking operator (attempt {attempt})", "warn")
                    req = registry.open_captcha(self.run, img, guess, attempt)
                    value = req.wait(timeout=op_to) or ""
                    registry.close_captcha(self.run)
                    value = value or guess
                elif not captcha_ocr.looks_plausible(guess):
                    self._log(f"OCR guess '{guess}' looks weak; submitting anyway (attempt {attempt})", "warn")
                value = captcha_ocr.clean(value)
                self._log(f"Captcha attempt {attempt}/{retries}: submitting '{value}'")

                self._set_input(self.sel["captcha_input"], value)
                try:
                    with self.page.expect_navigation(wait_until="domcontentloaded", timeout=nav_to):
                        self.page.click(self.sel["apply_button"])
                except PWTimeout:
                    self._log("Apply did not trigger a page load", "warn")

                if self._wait_report_ready():
                    self._log("Report rendered - captcha accepted")
                    return

                err = self._captcha_error_text()
                self._dump_debug(f"apply_fail_{attempt}")
                if err:
                    self._log(f"Captcha/portal rejected: {err!r}", "warn")
                else:
                    self._log(f"No report grid after Apply (url={self.page.url}); retrying", "warn")
                if reassert and self._reassert_filters_present():
                    reassert()
                self._refresh_captcha()
            except Exception as e:  # network blip / tab crash / etc - don't let it kill the whole run  # noqa: BLE001
                self._log(f"Captcha attempt {attempt} hit an error ({e}); retrying", "warn")
                try:
                    self.page.wait_for_timeout(5000)  # give a flaky connection a moment
                except Exception:
                    pass  # page itself is gone - the next attempt (or item) will fail fast
        raise CaptchaExhausted(f"Captcha validation failed {retries} times - logged, skipping item")

    def _wait_report_ready(self, budget_s: int = 5) -> bool:
        """Poll for the export button after the Apply POST re-render.

        Measured live against the real portal: a rejected captcha shows no
        error text at all (the form just silently re-renders blank), so
        there is no "it failed" signal to detect early - only "the button
        never showed up." Accepted captchas render the button in ~1.5-2.5s
        every time measured; a rejected one never produces it no matter how
        long you wait. The budget used to be 12s, which meant every one of
        the ~40% of attempts that fail (OCR accuracy on this captcha style
        is ~58%) wasted up to 12s doing nothing. 5s leaves >2x margin over
        the slowest observed real success."""
        deadline = time.time() + budget_s
        while time.time() < deadline:
            try:
                if self.page.locator(self.sel["download_excel"]).count() > 0:
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(300)
        return False

    def _dump_debug(self, tag: str) -> None:
        """Debug-only screenshot + HTML dump, off by default. It was running
        unconditionally on every failed captcha attempt in production - real
        I/O cost on ~40% of attempts for output nobody was looking at. Set
        VAHAN_DEBUG_DUMPS=1 to re-enable while calibrating against a live
        portal change."""
        if not os.environ.get("VAHAN_DEBUG_DUMPS"):
            return
        try:
            d = Path("calibration"); d.mkdir(exist_ok=True)
            self.page.screenshot(path=str(d / f"{tag}.png"), full_page=True)
            (d / f"{tag}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass

    def _captcha_error_text(self) -> str:
        for css in (self.sel["captcha_error"], self.sel["system_error"]):
            try:
                loc = self.page.locator(css).first
                if loc.count() and loc.is_visible():
                    t = (loc.inner_text() or "").strip()
                    if t:
                        return t
            except Exception:
                pass
        return ""

    # -------------------------------------------------------------- export
    def download_excel(self, dest: Path) -> Path:
        """Click 'Download All Records Excel' and save the file.

        For the Maker view the button runs an async paginated fetch then a
        client-side XLSX build, so it can take a while and occasionally needs a
        second click. We watch #makerExportStatus for progress/errors.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        to = self.cfg["portal"]["report_timeout"] * 1000
        status_css = self.sel.get("export_status", "#makerExportStatus")
        btn = self.sel["download_excel"]
        if os.environ.get("VAHAN_DEBUG_EXPORT"):
            self._dump_debug("report_ready")

        # The Maker view auto-runs its own pagination right after Apply; clicking
        # the export button while that is in flight is silently ignored (the JS
        # handler bails on a `loading`/`exportInProgress` guard). Wait for the
        # table to settle first.
        try:
            self.page.locator(self.sel.get("report_table", "#makerPagedReportTable")
                              + " tbody tr").first.wait_for(state="attached", timeout=15_000)
        except Exception:
            pass
        self.page.wait_for_timeout(2000)

        # Empty report (a State-month with no LMV/LPV registrations): the grid has
        # only a "Maker" header and no rows. Clicking export then spins forever /
        # crashes the tab - just write an empty file and move on.
        if self._report_is_empty():
            self._write_empty_xlsx(dest)
            self._log(f"{dest.name}: no records for this item - wrote empty file")
            return dest

        last_err = None
        STALL_S = 25  # batch-fetch status unchanged this long = genuinely stuck, not just slow
        for attempt in range(1, 5):
            try:
                with self.page.expect_download(timeout=to) as dl:
                    self.page.click(btn, force=True)
                    # Poll the batch-fetch status ourselves instead of blindly
                    # blocking on the download event for the full budget: a
                    # stuck export (status stops advancing, e.g. wedged on
                    # "Fetching batch 7...") used to burn the entire timeout
                    # doing nothing, four times over, before giving up. We
                    # break out early once status says "ready" (the download
                    # fires within a moment of that) or once it's provably
                    # stalled - a real success is unaffected either way.
                    last_status, last_change, registered = "", time.time(), False
                    deadline = time.time() + (to / 1000)
                    while time.time() < deadline:
                        self.page.wait_for_timeout(700)
                        st = self._text(status_css)
                        if st:
                            registered = True
                        if "ready" in st.lower():
                            break
                        if st != last_status:
                            last_status, last_change = st, time.time()
                        elif registered and (time.time() - last_change) > STALL_S:
                            raise PortalError(f"Export stalled at {st!r} for over {STALL_S}s")
                        elif not registered and (time.time() - last_change) > 5:
                            self._log(f"Export click {attempt} not registered; re-clicking", "warn")
                            self.page.click(btn, force=True)
                            last_change = time.time()
                    self._log(f"Export click {attempt}; status={self._text(status_css)!r}")
                download: Download = dl.value
                download.save_as(str(dest))
                if not dest.exists() or dest.stat().st_size == 0:
                    raise PortalError("downloaded file is empty")
                self._log(f"Downloaded {dest.name} ({dest.stat().st_size} bytes)")
                return dest
            except Exception as e:  # PWTimeout / PortalError / "Page crashed"  # noqa: BLE001
                last_err = e
                st = self._text(status_css)
                emsg = self._captcha_error_text() or st
                self._log(f"Export attempt {attempt} failed ({e}); status={st!r}", "warn")
                if emsg and ("no record" in emsg.lower() or "limit exceeded" in emsg.lower()):
                    raise PortalError(f"Export refused: {emsg}") from e
                if "crash" in str(e).lower():
                    # tab died - retrying on the same page is pointless
                    raise PortalError(f"Browser tab crashed during export of {dest.name}") from e
                if self._report_is_empty():
                    self._write_empty_xlsx(dest)
                    self._log(f"{dest.name}: export failed but report is empty - wrote empty file")
                    return dest
                try:
                    self.page.wait_for_timeout(2000)
                except Exception:
                    break
        raise PortalError(f"Download failed after retries: {last_err}")

    def _report_is_empty(self) -> bool:
        """True when the Maker grid rendered but has no data (only the 'Maker'
        header, zero rows) - a State/RTO-month with no registrations."""
        try:
            return bool(self.page.evaluate(
                """() => {
                    const t = document.querySelector('#makerPagedReportTable');
                    if (!t) return false;
                    const dataRows = t.querySelectorAll('tbody tr.report-data-row, tbody tr').length;
                    const headCols = t.querySelectorAll('thead th').length;
                    const info = (document.querySelector('#makerServerPageInfo')||{}).textContent || '';
                    return dataRows === 0 || headCols <= 1 || /page 0/i.test(info);
                }"""
            ))
        except Exception:
            return False

    @staticmethod
    def _write_empty_xlsx(dest: Path) -> None:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Maker Report"
        ws.append(["No records for this State/RTO in the selected period"])
        ws.append(["Maker", "Total"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        wb.save(dest)

    def _text(self, css: str) -> str:
        try:
            loc = self.page.locator(css).first
            if loc.count():
                return (loc.inner_text() or "").strip()
        except Exception:
            pass
        return ""

    def report_header_text(self) -> str:
        """The dynamic heading above the grid, e.g. '...RTO (JAIPUR (FIRST) RTO -
        RJ14) Wise Maker and Month Wise Data for Rajasthan (2026)'. Used to catch
        a State/RTO selection that silently didn't apply - the report would
        otherwise render (and download) for a *wider* scope than requested, with
        no error anywhere, corrupting reconciliation."""
        return self._text(self.sel.get("report_header", "#makerDynamicReportHeader"))
