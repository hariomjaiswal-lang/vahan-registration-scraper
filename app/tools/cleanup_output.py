"""Delete old run folders from output/, keeping the newest N per flow.

Safe by default: prints what it WOULD delete and does nothing until you pass
--apply. Only touches output/<run>_flow{1,2,3,4}_<timestamp>/ folders - never
config.yaml, .env, the venv, etc.

    python -m tools.cleanup_output                 # dry run (preview only)
    python -m tools.cleanup_output --apply          # actually delete
    python -m tools.cleanup_output --keep 5 --apply # keep fewer per flow
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import config
from backend.flows._resilience import find_flow1_dir, find_flow3_dir

_FLOWS = ["flow1", "flow2", "flow3", "flow4"]


def _protected_runs(output_dir: Path) -> set[Path]:
    """Run folders that must never be deleted regardless of age: whichever
    Flow 1 / Flow 3 output the reconciliation auto-discovery is currently
    using as a baseline. Losing these would silently break Flow 2/3/4's
    'compare against the last real run' logic."""
    protected = set()
    for finder in (find_flow1_dir, find_flow3_dir):
        files_dir = finder(str(output_dir))
        if files_dir:
            protected.add(files_dir.parent)  # files_dir is <run>/files
    return protected


def _folder_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _fmt(n_bytes: int) -> str:
    mb = n_bytes / (1024 * 1024)
    return f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=10, help="runs to keep per flow (default 10)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: preview only)")
    args = ap.parse_args()

    output_dir = Path(config.load()["paths"]["output_dir"])
    protected = _protected_runs(output_dir)
    if protected:
        print("Protected (active reconciliation baseline - never deleted):")
        for p in sorted(protected):
            print(f"  KEEP  {p.name}")

    total_deleted = 0
    total_freed = 0

    for flow in _FLOWS:
        runs = sorted(output_dir.glob(f"*_{flow}_*"), key=lambda p: p.stat().st_mtime)
        keepers = {r for r in runs if r in protected}
        candidates = [r for r in runs if r not in keepers]  # oldest -> newest
        slots_left = max(0, args.keep - len(keepers))
        keepers |= set(candidates[len(candidates) - slots_left :] if slots_left else [])
        to_delete = [r for r in runs if r not in keepers]

        if not to_delete:
            print(f"{flow}: {len(runs)} run(s) - within the keep-{args.keep} limit, nothing to do")
            continue
        print(f"\n{flow}: {len(runs)} run(s) - keeping {len(keepers)} "
              f"({'deleting' if args.apply else 'would delete'} {len(to_delete)}):")
        for r in to_delete:
            size = _folder_size(r)
            total_freed += size
            total_deleted += 1
            tag = "DELETED" if args.apply else "  [dry]"
            print(f"  {tag}  {r.name}  ({_fmt(size)})")
            if args.apply:
                shutil.rmtree(r, ignore_errors=True)

    verb = "Deleted" if args.apply else "Would delete"
    print(f"\n{verb} {total_deleted} run folder(s), "
          f"{'freed' if args.apply else 'would free'} {_fmt(total_freed)}")
    if not args.apply:
        print("This was a DRY RUN - nothing was touched. Re-run with --apply to actually delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
