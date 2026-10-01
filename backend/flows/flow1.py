"""Flow 1 - Vahan_StateMonthwise.

36 States/UTs, Y-Axis = Maker, X-Axis = Month-wise, Calendar Year.
One Excel per state -> rename -> zip. Plus an All-India file (all states
selected) used only for the MB / BMW reconciliation and excluded from the zip.
"""
from __future__ import annotations

import time
from pathlib import Path

from backend import cloud_storage, config, packaging, validation
from backend.emailer import send_run_summary
from backend.flows._resilience import assert_scope, note_failure, note_success
from backend.portal import CaptchaExhausted, PortalError, VahanPortal
from backend.registry import Run, cancel_requested


def run_flow1(run: Run, cfg: dict) -> None:
    config.ensure_dirs()
    year = str(time.localtime().tm_year)
    out_root = Path(cfg["paths"]["output_dir"]) / f"{run.id}_flow1_{packaging.timestamp_slug()}"
    files_dir = out_root / "files"
    india_dir = out_root / "all_india"
    files_dir.mkdir(parents=True, exist_ok=True)
    india_dir.mkdir(parents=True, exist_ok=True)

    params = run.params or {}
    # "all" / "*" / blank in the states box means "no limit" (run all 36).
    _NO_LIMIT = {"", "all", "all states", "*", "36", "everything", "none"}
    only_raw: list[str] | None = params.get("states") or None
    only: list[str] | None = None
    if only_raw:
        cleaned = [s for s in only_raw if s.strip().lower() not in _NO_LIMIT]
        only = cleaned or None
    skip_all_india: bool = bool(params.get("skip_all_india"))
    skip_recon: bool = bool(params.get("skip_reconciliation"))

    succeeded: list[Path] = []
    failed: list[str] = []
    all_india_path: Path | None = None
    run.started = time.time()
    run.state = "running"

    aborted = False
    with VahanPortal(cfg, run) as portal:
      try:
        portal.open_report_page()

        def reassert() -> None:
            portal.apply_common_filters()
            portal.set_axes_maker_monthwise()

        reassert()

        all_states = portal.state_options()  # [{code, name}, ...]
        run.log(f"Portal lists {len(all_states)} states")
        if not all_states:
            raise PortalError(
                "Could not read the state list from #stateName - portal DOM may "
                "have changed; re-run tools.calibrate"
            )
        states = all_states
        if only:
            keys = {s.strip().lower() for s in only}

            def _match(s: dict) -> bool:
                code, name = s["code"].lower(), s["name"].lower()
                padded = f" {name} "
                return any(
                    k == code                       # exact state code, e.g. "ga"
                    or k == name                    # exact full name
                    or name.startswith(k + " ")     # prefix, e.g. "andaman"
                    or f" {k} " in padded           # whole word inside the name
                    for k in keys
                )

            states = [s for s in all_states if _match(s)]
            if not states:
                raise PortalError(
                    f"State filter {only!r} matched none of the portal's states. "
                    f"Valid codes: {', '.join(s['code'] for s in all_states)}"
                )
            run.log(f"Limited to {[s['code'] for s in states]}")

        run.progress_total = len(states) + (0 if skip_all_india else 1)
        consecutive_fail = 0
        # A long-lived browser session on this portal appears to get worse at
        # solving its own captchas the longer it runs (measured live: 0
        # failures in the first ~250s/19 states of a run, then a cluster of
        # failures for the rest - reproduced twice, not explained by machine
        # load). Proactively reopening the page every REFRESH_EVERY states
        # resets whatever that is, instead of only reacting after failures
        # start.
        REFRESH_EVERY = 12

        for i, st in enumerate(states):
            if cancel_requested(run.id):
                run.log("Cancellation requested - stopping", "warn")
                run.state = "cancelled"
                return
            if i > 0 and i % REFRESH_EVERY == 0:
                run.log(f"Refreshing portal session after {i} states (preventive)", "info")
                portal.open_report_page()
                reassert()
            code, name = st["code"], st["name"]
            run.current_item = f"{name} ({code})"
            dest = files_dir / f"Monthwise_{year}-{code}.xlsx"
            try:
                portal.select_only_state(code)
                portal.solve_captcha_and_apply(
                    reassert=lambda c=code: (reassert(), portal.select_only_state(c))
                )
                assert_scope(portal.report_header_text(), [name], name)
                portal.download_excel(dest)
                succeeded.append(dest)
                run.log(f"OK  {name} -> {dest.name}")
                consecutive_fail = note_success(consecutive_fail)
            except Exception as e:  # noqa: BLE001 - never let one item kill the whole run
                failed.append(f"{name} ({code}): {e}")
                run.log(f"FAIL {name}: {e}", "error")
                consecutive_fail = note_failure(portal, run, consecutive_fail, on_recover=reassert)
            finally:
                run.progress_done += 1

        # ---- All-India reference file (all states selected, not zipped) ------
        all_india_path: Path | None = None
        if not skip_all_india and not cancel_requested(run.id):
            run.current_item = "All India"
            all_india_path = india_dir / f"Monthwise_{year}-ALL-INDIA.xlsx"
            try:
                all_codes = [s["code"] for s in portal.state_options()]
                portal.set_multi(portal.sel["state_select"], all_codes)
                portal.page.wait_for_timeout(300)
                portal.solve_captcha_and_apply(
                    reassert=lambda: (
                        reassert(),
                        portal.set_multi(portal.sel["state_select"], all_codes),
                    )
                )
                portal.download_excel(all_india_path)
                run.log("OK  All-India reference file")
            except Exception as e:  # noqa: BLE001
                failed.append(f"All India: {e}")
                run.log(f"FAIL All India: {e}", "error")
                all_india_path = None
            finally:
                run.progress_done += 1
      except Exception as e:  # noqa: BLE001 - package whatever succeeded, never lose the work
        aborted = True
        failed.append(f"RUN STOPPED EARLY: {e}")
        run.log(f"Run loop stopped early ({e}); packaging the {len(succeeded)} file(s) "
                f"already downloaded", "error")

    # ---- Stage 8: zip + reconcile + summary (always runs, even after aborted) ----
    run.current_item = "packaging"
    zip_path = out_root / f"Flow1_StateMonthwise_{year}_{packaging.timestamp_slug()}.zip"
    packaging.make_zip(succeeded, zip_path)

    reconciliation = {"ok": None, "rows": [], "file_errors": [], "skipped": True}
    partial = only is not None  # a limited-states run can never equal All-India
    if partial:
        run.log(
            f"Reconciliation N/A - partial run ({len(succeeded)} of 36 states); "
            "run all states (blank filter) for the MB/BMW check",
        )
    elif not skip_recon and all_india_path and succeeded:
        try:
            reconciliation = validation.reconcile(
                succeeded, all_india_path, cfg["reconciliation"]["buckets"]
            )
            reconciliation["skipped"] = False
            run.log(
                "Reconciliation " + ("PASSED" if reconciliation["ok"] else "MISMATCH - see summary"),
                "info" if reconciliation["ok"] else "warn",
            )
        except Exception as e:  # noqa: BLE001
            reconciliation = {"ok": False, "rows": [], "file_errors": [str(e)], "skipped": False}
            run.log(f"Reconciliation error: {e}", "error")

    if partial:
        recon_label = "n/a (partial run)"
    elif reconciliation.get("skipped"):
        recon_label = "skipped"
    elif reconciliation.get("ok") is True:
        recon_label = "pass"
    elif reconciliation.get("ok") is False:
        recon_label = "mismatch"
    else:
        recon_label = "n/a"
    reconciliation["label"] = recon_label

    run.finished = time.time()
    summary_path = out_root / f"Flow1_Summary_{packaging.timestamp_slug()}.xlsx"
    packaging.write_summary_xlsx(
        summary_path,
        flow="Flow 1 - Vahan_StateMonthwise",
        run_id=run.id,
        started=run.started,
        finished=run.finished,
        expected=run.progress_total - (0 if skip_all_india else 1),
        succeeded=len(succeeded),
        failed_items=failed,
        reconciliation=reconciliation,
    )
    cloud_storage.upload_file(zip_path, f"{run.id}/{zip_path.name}")
    cloud_storage.upload_file(summary_path, f"{run.id}/{summary_path.name}")

    run.result = {
        "output_dir": str(out_root),
        "zip": str(zip_path),
        "summary": str(summary_path),
        "all_india_file": str(all_india_path) if all_india_path else None,
        "succeeded": len(succeeded),
        "failed": failed,
        "partial_run": partial,
        "reconciliation_ok": reconciliation.get("ok"),
        "reconciliation_label": recon_label,
        "reconciliation_rows": reconciliation.get("rows", []),
        "aborted": aborted,
    }
    send_run_summary(
        run,
        flow_title="Flow 1 - Vahan_StateMonthwise",
        zip_path=zip_path,
        summary_path=summary_path,
        recon_label=recon_label,
        succeeded=len(succeeded),
        failed_count=len(failed),
    )
    run.state = "done"
    run.current_item = ""
    run.log(
        f"Flow 1 complete: {len(succeeded)} files, {len(failed)} failures"
        + (" (run stopped early - see log)" if aborted else "")
        + f". Zip: {zip_path.name}"
    )
