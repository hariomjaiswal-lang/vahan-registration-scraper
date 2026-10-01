"""Business reconciliation from PDD Step 8.2.

Flow 1 export layout: Y-Axis = Maker (rows), X-Axis = Month-wise (columns).
We sum the MB and BMW maker buckets per month across every State/UT file and
check the totals against the 'All India' file (states = All), which is itself
kept out of the final zip.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _month_key(text: str) -> str | None:
    if not text:
        return None
    t = str(text).strip().upper()
    for m in _MONTHS:
        if m in t:
            return m
    return None


def _to_int(v) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    digits = re.sub(r"[^\d-]", "", str(v))
    return int(digits) if digits and digits != "-" else 0


def parse_maker_month(path: Path) -> dict[str, dict[str, int]]:
    """Return {maker_name_upper: {MON: count}} for one exported workbook."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx = None
    for i, row in enumerate(rows):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if any(c == "maker" for c in cells):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"No 'Maker' header row found in {path.name}")

    header = rows[header_idx]
    maker_col = next(
        j for j, c in enumerate(header) if c is not None and str(c).strip().lower() == "maker"
    )
    month_cols: dict[int, str] = {}
    for j, c in enumerate(header):
        mk = _month_key(c)
        if mk:
            month_cols[j] = mk

    out: dict[str, dict[str, int]] = {}
    for row in rows[header_idx + 1 :]:
        if row is None or maker_col >= len(row):
            continue
        name = row[maker_col]
        if not name or not str(name).strip():
            continue
        key = str(name).strip().upper()
        if key in ("TOTAL", "GRAND TOTAL"):
            continue
        bucket = out.setdefault(key, {})
        for j, mon in month_cols.items():
            if j < len(row):
                bucket[mon] = bucket.get(mon, 0) + _to_int(row[j])
    return out


def parse_maker_grand_total(path: Path) -> dict[str, int]:
    """Return {maker_name_upper: grand_total} - the sum of every numeric cell on
    that maker's row. Works for any Maker-view export (Maker x Month, Maker x
    Fuel, ...) because we don't care which columns they are."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx = None
    for i, row in enumerate(rows):
        if any(c is not None and str(c).strip().lower() == "maker" for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"No 'Maker' header row found in {path.name}")

    header = rows[header_idx]
    maker_col = next(
        j for j, c in enumerate(header) if c is not None and str(c).strip().lower() == "maker"
    )
    total_col = next(
        (j for j, c in enumerate(header) if c is not None and str(c).strip().lower() == "total"),
        None,
    )

    out: dict[str, int] = {}
    for row in rows[header_idx + 1 :]:
        if row is None or maker_col >= len(row):
            continue
        name = row[maker_col]
        if not name or not str(name).strip():
            continue
        key = str(name).strip().upper()
        if key in ("TOTAL", "GRAND TOTAL", "PAGE TOTAL"):
            continue
        if total_col is not None and total_col < len(row):
            val = _to_int(row[total_col])
        else:  # no Total column - sum the numeric cells after the maker name
            val = sum(_to_int(row[j]) for j in range(maker_col + 1, len(row)))
        out[key] = out.get(key, 0) + val
    return out


def _norm_maker(s: str) -> str:
    """Uppercase, collapse whitespace, normalise dash spacing so
    'MERCEDES -BENZ AG' == 'MERCEDES BENZ - AG' == 'MERCEDES-BENZ AG'."""
    s = re.sub(r"\s*-\s*", " ", str(s).upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def bucket_grand_total(maker_totals: dict[str, int], names: list[str]) -> int:
    wanted = {_norm_maker(n) for n in names}
    return sum(v for m, v in maker_totals.items() if _norm_maker(m) in wanted)


def _bucket_totals(maker_data: dict[str, dict[str, int]], names: list[str]) -> dict[str, int]:
    wanted = {_norm_maker(n) for n in names}
    totals: dict[str, int] = {}
    for maker, months in maker_data.items():
        if _norm_maker(maker) in wanted:
            for mon, cnt in months.items():
                totals[mon] = totals.get(mon, 0) + cnt
    return totals


def reconcile(
    state_files: list[Path], all_india_file: Path, buckets: dict[str, list[str]]
) -> dict:
    """Compare summed State/UT bucket totals per month vs the All-India file."""
    agg: dict[str, dict[str, int]] = {}  # bucket -> {MON: sum over states}
    per_state_errors: list[str] = []

    for f in state_files:
        try:
            data = parse_maker_month(f)
        except Exception as e:  # noqa: PERF203
            per_state_errors.append(f"{f.name}: {e}")
            continue
        for bname, names in buckets.items():
            bt = _bucket_totals(data, names)
            dst = agg.setdefault(bname, {})
            for mon, cnt in bt.items():
                dst[mon] = dst.get(mon, 0) + cnt

    all_india_data = parse_maker_month(all_india_file)
    india_totals = {
        bname: _bucket_totals(all_india_data, names) for bname, names in buckets.items()
    }

    lines: list[dict] = []
    ok = True
    for bname in buckets:
        months = sorted(
            set(agg.get(bname, {})) | set(india_totals.get(bname, {})),
            key=_MONTHS.index,
        )
        for mon in months:
            s = agg.get(bname, {}).get(mon, 0)
            i = india_totals.get(bname, {}).get(mon, 0)
            match = s == i
            ok = ok and match
            lines.append(
                {
                    "bucket": bname,
                    "month": mon,
                    "sum_of_states": s,
                    "all_india": i,
                    "delta": s - i,
                    "match": match,
                }
            )

    return {
        "ok": ok and not per_state_errors,
        "rows": lines,
        "file_errors": per_state_errors,
        "states_counted": len(state_files) - len(per_state_errors),
    }


def _as_list(v) -> list[Path]:
    if isinstance(v, (list, tuple, set)):
        return [Path(p) for p in v]
    return [Path(v)]


def reconcile_flow2(
    a_by_state: dict[str, list[Path]],
    b_by_state: dict[str, "Path | list[Path]"],
    buckets: dict[str, list[str]],
    *,
    a_key: str = "flow2_ytd",
    b_key: str = "flow1_total",
) -> dict:
    """PDD Step 8.2, the shape shared by Flows 2/3/4: per State/UT and bucket,
    the sum of grand totals on side A must equal the sum of grand totals on
    side B - which flow is "A" and which is "B" doesn't matter to the maths.

        Flow 2 vs Flow 1: a = {state: [Fuelwise month files]},        b = {state: Monthwise state file}
        Flow 3 vs Flow 1: a = {state: [Monthwise RTO files]},         b = {state: Monthwise state file}
        Flow 4 vs Flow 3: a = {state: [Fuelwise RTO-month files]},    b = {state: [Monthwise RTO files]}

    `b_by_state` values may be a single Path or a list of Paths (summed
    together) - that's what lets Flow 3's many-files-per-state stand in as a
    baseline for Flow 4, the same way Flow 1's one-file-per-state does for 2/3.
    """
    lines: list[dict] = []
    errors: list[str] = []
    ok = True

    for code in sorted(a_by_state):
        b_files = _as_list(b_by_state[code]) if code in b_by_state else []
        b_files = [p for p in b_files if p.exists()]
        if not b_files:
            errors.append(f"{code}: no matching baseline file")
            ok = False
            continue
        try:
            b_tot_parts = [parse_maker_grand_total(p) for p in b_files]
            a_tots = [parse_maker_grand_total(p) for p in a_by_state[code]]
        except Exception as e:  # noqa: PERF203
            errors.append(f"{code}: {e}")
            ok = False
            continue
        for bname, names in buckets.items():
            b_val = sum(bucket_grand_total(t, names) for t in b_tot_parts)
            a_val = sum(bucket_grand_total(t, names) for t in a_tots)
            match = a_val == b_val
            ok = ok and match
            lines.append(
                {
                    "state": code,
                    "bucket": bname,
                    a_key: a_val,
                    b_key: b_val,
                    "delta": a_val - b_val,
                    "match": match,
                    "months": len(a_by_state[code]),  # kept for the Reconciliation sheet column
                }
            )

    return {
        "ok": ok and not errors,
        "rows": lines,
        "file_errors": errors,
        "states_counted": len(a_by_state) - len(errors),
    }
