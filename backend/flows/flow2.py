"""Flow 2 - Vahan_StateFuelwise.

For every State/UT and every month Jan..current: Y-Axis = Maker, X-Axis = Fuel,
period = "1 Month Flexible" set to that whole month. One Excel per State-month
-> rename -> zip.

Reconciliation (PDD Step 8.2): per State and bucket, the sum of the Flow 2
monthly totals (YTD) must equal the Flow 1 (StateMonthwise) state total. Needs a
Flow 1 output folder - auto-discovered, or passed as `flow1_dir`.
"""
from __future__ import annotations

import time
from pathlib import Path

from backend import cloud_storage, config, packaging, validation
from backend.emailer import send_run_summary
from backend.flows._resilience import assert_scope, find_flow1_dir, note_failure, note_success
from backend.portal import CaptchaExhausted, PortalError, VahanPortal
from backend.registry import Run, cancel_requested

_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _months_arg(value, current_month: int) -> list[int]:
    """Accept ['JAN','FEB'] / [1,2] / None -> list of 1-based month numbers."""
    if not value:
        return list(range(1, current_month + 1))
    out: list[int] = []
    for v in value:
        s = str(v).strip().upper()
        if s.isdigit():
            out.append(int(s))
        elif s[:3] in _MON:
            out.append(_MON.index(s[:3]) + 1)
    return sorted(set(m for m in out if 1 <= m <= 12))


def run_flow2(run: Run, cfg: dict) -> None:
    config.ensure_dirs()
    now = time.localtime()
    year = int((run.params or {}).get("year") or now.tm_year)
    months = _months_arg((run.params or {}).get("months"), now.tm_mon if year == now.tm_year else 12)

    out_root = Path(cfg["paths"]["output_dir"]) / f"{run.id}_flow2_{packaging.timestamp_slug()}"
    files_dir = out_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    params = run.params or {}
    only = params.get("states") or None
    _NO_LIMIT = {"", "all", "all states", "*", "36"}
    if only:
        only = [s for s in only if s.strip().lower() not in _NO_LIMIT] or None
    skip_recon = bool(params.get("skip_reconciliation"))
    flow1_dir = params.get("flow1_dir")

    succeeded: list[Path] = []
    per_state_files: dict[str, list[Path]] = {}
    failed: list[str] = []
    run.started = time.time()
    run.state = "running"

    aborted = False
    with VahanPortal(cfg, run) as portal:
      try:
        portal.open_report_page()

        def reassert() -> None:
            portal.apply_common_filters(calendar_year=False)
            portal.set_axes_maker_fuel()

        reassert()

        all_states = portal.state_options()
        run.log(f"Portal lists {len(all_states)} states")
        states = all_states
        if only:
            keys = {s.strip().lower() for s in only}

            def _m(s: dict) -> bool:
                code, name = s["code"].lower(), f" {s['name'].lower()} "
                return any(k == code or k.strip() == name.strip()
                           or name.startswith(f" {k} ") or f" {k} " in name for k in keys)

            states = [s for s in all_states if _m(s)]
            if not states:
                raise PortalError(
                    f"State filter {only!r} matched nothing. Valid codes: "
                    + ", ".join(s["code"] for s in all_states)
                )
        run.log(f"States: {[s['code'] for s in states]}  Months: {[_MON[m-1] for m in months]}")
        run.progress_total = len(states) * len(months)
        # See flow1.py: a long-lived session on this portal gets measurably
        # worse at solving its own captchas the longer it runs. Proactively
        # reopening the page every REFRESH_EVERY items resets that.
        REFRESH_EVERY = 12
        item_count = 0

        for st in states:
            if cancel_requested(run.id):
                run.state = "cancelled"
                return
            code, name = st["code"], st["name"]
            per_state_files[code] = []
            portal.select_only_state(code)
            consecutive_fail = 0
            for m in months:
                if cancel_requested(run.id):
                    run.state = "cancelled"
                    return
                if item_count > 0 and item_count % REFRESH_EVERY == 0:
                    run.log(f"Refreshing portal session after {item_count} items (preventive)", "info")
                    portal.open_report_page()
                    reassert()
                    portal.select_only_state(code)
                item_count += 1
                mon = _MON[m - 1]
                run.current_item = f"{name} ({code}) - {mon} {year}"
                dest = files_dir / f"Fuelwise_{year}-{mon}-{code}.xlsx"
                try:
                    portal.set_month_period(year, m)
                    portal.solve_captcha_and_apply(
                        reassert=lambda c=code, mm=m: (
                            reassert(), portal.select_only_state(c),
                            portal.set_month_period(year, mm),
                        )
                    )
                    assert_scope(portal.report_header_text(), [name], f"{name} {mon}")
                    portal.download_excel(dest)
                    succeeded.append(dest)
                    per_state_files[code].append(dest)
                    run.log(f"OK  {code} {mon} -> {dest.name}")
                    consecutive_fail = note_success(consecutive_fail)
                except Exception as e:  # noqa: BLE001 - never let one item kill the whole run
                    failed.append(f"{code} {mon}: {e}")
                    run.log(f"FAIL {code} {mon}: {e}", "error")
                    consecutive_fail = note_failure(
                        portal, run, consecutive_fail,
                        on_recover=lambda c=code: (reassert(), portal.select_only_state(c)),
                    )
                finally:
                    run.progress_done += 1
      except Exception as e:  # noqa: BLE001 - package whatever succeeded, never lose the work
        aborted = True
        failed.append(f"RUN STOPPED EARLY: {e}")
        run.log(f"Run loop stopped early ({e}); packaging the {len(succeeded)} file(s) "
                f"already downloaded", "error")

    # ---- Stage 8: zip + reconcile + summary (always runs, even after aborted) ----
    run.current_item = "packaging"
    zip_path = out_root / f"Flow2_StateFuelwise_{year}_{packaging.timestamp_slug()}.zip"
    packaging.make_zip(succeeded, zip_path)

    full_ytd = months == list(range(1, (now.tm_mon if year == now.tm_year else 12) + 1))
    reconciliation = {"ok": None, "rows": [], "file_errors": [], "skipped": True}
    f1_dir = Path(flow1_dir) if flow1_dir else find_flow1_dir(cfg["paths"]["output_dir"])
    recon_label = "skipped"

    if skip_recon:
        recon_label = "skipped"
    elif not full_ytd:
        recon_label = "n/a (partial months)"
        run.log("Reconciliation N/A - not a full Jan..current run", "info")
    elif not f1_dir or not f1_dir.is_dir():
        recon_label = "n/a (no Flow 1 output)"
        run.log("Reconciliation N/A - run Flow 1 first (or pass flow1_dir)", "warn")
    elif succeeded:
        flow1_by_state = {
            code: f1_dir / f"Monthwise_{year}-{code}.xlsx" for code in per_state_files
        }
        try:
            reconciliation = validation.reconcile_flow2(
                {k: v for k, v in per_state_files.items() if v},
                flow1_by_state,
                cfg["reconciliation"]["buckets"],
            )
            reconciliation["skipped"] = False
            recon_label = "pass" if reconciliation["ok"] else "mismatch"
            run.log(
                f"Reconciliation (vs Flow 1 @ {f1_dir.parent.name}) "
                + ("PASSED" if reconciliation["ok"] else "MISMATCH - see summary"),
                "info" if reconciliation["ok"] else "warn",
            )
        except Exception as e:  # noqa: BLE001
            reconciliation = {"ok": False, "rows": [], "file_errors": [str(e)], "skipped": False}
            recon_label = "mismatch"
            run.log(f"Reconciliation error: {e}", "error")
    reconciliation["label"] = recon_label

    run.finished = time.time()
    summary_path = out_root / f"Flow2_Summary_{packaging.timestamp_slug()}.xlsx"
    packaging.write_summary_xlsx(
        summary_path,
        flow="Flow 2 - Vahan_StateFuelwise",
        run_id=run.id,
        started=run.started,
        finished=run.finished,
        expected=run.progress_total,
        succeeded=len(succeeded),
        failed_items=failed,
        reconciliation=reconciliation,
        recon_columns=("State", "Bucket", "Flow 2 YTD", "Flow 1 Total", "Delta", "Match"),
        recon_row_keys=("state", "bucket", "flow2_ytd", "flow1_total", "delta", "match"),
    )
    cloud_storage.upload_file(zip_path, f"{run.id}/{zip_path.name}")
    cloud_storage.upload_file(summary_path, f"{run.id}/{summary_path.name}")

    run.result = {
        "output_dir": str(out_root),
        "zip": str(zip_path),
        "summary": str(summary_path),
        "succeeded": len(succeeded),
        "failed": failed,
        "partial_run": bool(only) or not full_ytd,
        "reconciliation_ok": reconciliation.get("ok"),
        "reconciliation_label": recon_label,
        "reconciliation_rows": reconciliation.get("rows", []),
        "flow1_dir": str(f1_dir) if f1_dir else None,
        "aborted": aborted,
    }
    send_run_summary(
        run,
        flow_title="Flow 2 - Vahan_StateFuelwise",
        zip_path=zip_path,
        summary_path=summary_path,
        recon_label=recon_label,
        succeeded=len(succeeded),
        failed_count=len(failed),
    )
    run.state = "done"
    run.current_item = ""
    run.log(
        f"Flow 2 complete: {len(succeeded)} files, {len(failed)} failures"
        + (" (run stopped early - see log)" if aborted else "")
        + f". Zip: {zip_path.name}"
    )
