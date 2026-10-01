"""Headless CLI entry point for scheduled runs (PDD: Flow 1 daily / ad hoc).

Examples:
  python -m tools.run_flow flow1_state_monthwise
  python -m tools.run_flow flow1_state_monthwise --states Goa,Chandigarh --skip-all-india
  python -m tools.run_flow flow2_state_fuelwise --states GA --months SEP
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import config
from backend.flows import REGISTRY
from backend.registry import create_run, persist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("flow", choices=sorted(REGISTRY))
    ap.add_argument("--states", help="comma separated subset")
    ap.add_argument("--skip-all-india", action="store_true")
    ap.add_argument("--skip-reconciliation", action="store_true")
    ap.add_argument("--months", help="flow2: comma list e.g. SEP or JAN,FEB (default Jan..now)")
    ap.add_argument("--year", type=int, help="flow2/3: report year (default current)")
    ap.add_argument("--flow1-dir", help="flow2/3: path to a Flow 1 run's files/ folder")
    ap.add_argument("--flow3-dir", help="flow4: path to a Flow 3 run's files/ folder")
    ap.add_argument("--rtos", help="flow3/4: comma list of RTO codes to limit the run")
    ap.add_argument("--max-rtos-per-state", type=int, help="flow3/4: cap RTOs per state (smoke test)")
    ap.add_argument(
        "--manual-captcha",
        action="store_true",
        help="pause for an operator when OCR is unsure (needs the dashboard); "
        "default for CLI is OCR-only",
    )
    args = ap.parse_args()

    cfg = config.load()
    if not args.manual_captcha:
        cfg.setdefault("captcha", {})["manual_fallback"] = False

    params: dict = {}
    if args.states:
        params["states"] = [s.strip() for s in args.states.split(",") if s.strip()]
    if args.skip_all_india:
        params["skip_all_india"] = True
    if args.skip_reconciliation:
        params["skip_reconciliation"] = True
    if args.months:
        params["months"] = [s.strip() for s in args.months.split(",") if s.strip()]
    if args.year:
        params["year"] = args.year
    if args.flow1_dir:
        params["flow1_dir"] = args.flow1_dir
    if args.flow3_dir:
        params["flow3_dir"] = args.flow3_dir
    if args.rtos:
        params["rtos"] = [s.strip() for s in args.rtos.split(",") if s.strip()]
    if args.max_rtos_per_state:
        params["max_rtos_per_state"] = args.max_rtos_per_state

    run = create_run(args.flow, params, source="cli")
    print(f"Run {run.id} -> {args.flow}  params={params}")
    started = time.time()
    try:
        REGISTRY[args.flow](run, cfg)
    except Exception:  # noqa: BLE001
        run.state = "error"
        raise
    finally:
        run.finished = run.finished or time.time()
        persist(run)
        for line in run.logs:
            print(f"[{line.level}] {line.msg}")
    print(f"\nState: {run.state}  ({time.time()-started:.0f}s)")
    if run.result:
        print("Result:")
        for k, v in run.result.items():
            if k != "reconciliation_rows":
                print(f"  {k}: {v}")
    return 0 if run.state == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
