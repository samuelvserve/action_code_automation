"""
build_report.py
===============
Standalone management **report builder** for the Action Code Dashboard.

This is a SEPARATE process from the ACR automation. It deliberately does NOT
import, call, or modify ``action_code_pipeline.py`` (or the Flask app / SQLite
DB). It only *reads* a finished Master File workbook and writes a new,
self-contained summary workbook next to it.

Run it by hand, or from a scheduler, at any time after a Master File exists:

    python build_report.py                     # newest Master File in outputs/
    python build_report.py path/to/Master.xlsx
    python build_report.py path/to/Master.xlsx -o my_reports

Output (unless --no-timestamp):
    outputs/reports/Report_<source>_<YYYYmmdd_HHMMSS>.xlsx

Workbook sheets
---------------
    Overview     - run-at-a-glance: source file, counts, totals, distributions
    Action Items - urgent expedite candidates (ABC A/B, Action Code 1/2,
                   DOS OH < 21) - the same rule the Master File highlights
    By ABC       - rows / demand / on-hand / on-order per ABC class
    By Vendor    - top vendors by demand, with action-item counts
    By Plant     - rows / on-hand / on-order / demand per plant
    Data         - the full Master File rows, unchanged

The input column layout matches action_code_pipeline.MASTER_COLUMNS, including
the trailing space in ``Current Corp OH ``. Missing columns are tolerated so
the script keeps working if the Master File evolves.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Paths (resolved against this file so it works from any cwd)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
REPORT_DIR = OUTPUT_DIR / "reports"

# Master File column names. "Current Corp OH " intentionally keeps its
# trailing space (see action_code_pipeline.MASTER_COLUMNS).
COL_FERT = "Fert#"
COL_DESC = "Material Description"
COL_VENDOR_NUM = "Vendor number"
COL_VENDOR = "Vendor name"
COL_ACTION = "Action Code"
COL_ABC = "ABC"
COL_PLANT = "Plant"
COL_DEMAND = "Total Demand"
COL_FERT_OH = "Fert OH"
COL_RAW_OH = "Raw OH"
COL_CORP_OH = "Current Corp OH "
COL_OO = "Current OO"
COL_DOS_OH = "DOS OH"
COL_DOS_OO = "DOS OO"

# Excel highlight rule used by the Master File: A/B class + Action Code 1/2 +
# DOS OH < 21 days = urgent expedite candidate.
URGENT_ABC = ("A", "B")
URGENT_ACTION = (1, 2)
URGENT_DOS_OH = 21

# Styling (kept in the same family as the Master File for a consistent look).
BLUE = "FF83CBEB"
ORANGE = "FFF2AA84"
RED_FILL = "FFFF0000"
WHITE = "FFFFFFFF"
FONT_NAME = "Aptos Narrow"
FONT_SIZE = 11


# ---------------------------------------------------------------------------
# Loading & helpers
# ---------------------------------------------------------------------------
def find_latest_master_file() -> Path:
    """Newest Master_File_Action_Code*.xlsx in outputs/, if any."""
    candidates = sorted(
        OUTPUT_DIR.glob("Master_File_Action_Code*.xlsx"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No Master_File_Action_Code*.xlsx found in {OUTPUT_DIR}. "
            "Pass an explicit path to a Master File."
        )
    return candidates[-1]


def load_master(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Master File not found: {path}")
    df = pd.read_excel(path, sheet_name=0)
    # Normalize the trailing/leading whitespace that can sneak in via copy/paste
    # while preserving the canonical "Current Corp OH " name.
    df.columns = [str(c) for c in df.columns]
    return df


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _abc(df: pd.DataFrame) -> pd.Series:
    if COL_ABC in df.columns:
        return df[COL_ABC].astype(str).str.strip()
    return pd.Series([""] * len(df), index=df.index)


def urgent_mask(df: pd.DataFrame) -> pd.Series:
    """A/B class + Action Code 1/2 + DOS OH < 21 (mirrors the Excel rule)."""
    abc = _abc(df)
    action = _num(df[COL_ACTION]) if COL_ACTION in df.columns else pd.Series(np.nan, index=df.index)
    dos = _num(df[COL_DOS_OH]) if COL_DOS_OH in df.columns else pd.Series(np.nan, index=df.index)
    return abc.isin(URGENT_ABC) & action.isin(URGENT_ACTION) & (dos < URGENT_DOS_OH)


def _safe(df: pd.DataFrame, col: str, default=0) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


# ---------------------------------------------------------------------------
# Sheet builders (return plain DataFrames; written + styled later)
# ---------------------------------------------------------------------------
def build_overview(source: Path, df: pd.DataFrame) -> pd.DataFrame:
    abc = _abc(df)
    rows = len(df)
    urgent = int(urgent_mask(df).sum())
    demand = float(_num(_safe(df, COL_DEMAND)).fillna(0).sum())
    corp_oh = float(_num(_safe(df, COL_CORP_OH)).fillna(0).sum())
    on_order = float(_num(_safe(df, COL_OO)).fillna(0).sum())

    items = [
        ("Report generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Source Master File", source.name),
        ("Source rows", rows),
        ("Unique materials (Fert#)", int(_safe(df, COL_FERT).nunique())),
        ("Unique vendors", int(_safe(df, COL_VENDOR).dropna().nunique())),
        ("Plants", int(_num(_safe(df, COL_PLANT)).dropna().nunique())),
        ("Total Demand (sum)", round(demand, 2)),
        ("Current Corp OH (sum)", round(corp_oh, 2)),
        ("Current OO (sum)", round(on_order, 2)),
        ("Urgent action items (A/B, code 1/2, DOS OH < 21)", urgent),
        ("", ""),
        ("ABC A", int((abc == "A").sum())),
        ("ABC B", int((abc == "B").sum())),
        ("ABC C", int((abc == "C").sum())),
        ("ABC D", int((abc == "D").sum())),
        ("ABC N / other", int((~abc.isin(["A", "B", "C", "D"])).sum())),
    ]
    return pd.DataFrame(items, columns=["Metric", "Value"])


def build_action_items(df: pd.DataFrame) -> pd.DataFrame:
    out = df[urgent_mask(df)].copy()
    if out.empty:
        return out
    out["_dos"] = _num(out[COL_DOS_OH]) if COL_DOS_OH in out.columns else np.nan
    out = out.sort_values("_dos", ascending=True, na_position="last").drop(columns=["_dos"])
    return out.reset_index(drop=True)


def _group_summary(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Generic per-group rollup: rows, demand, on-hand, on-order, action items."""
    if key not in df.columns or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["_abc"] = _abc(work)
    work["_demand"] = _num(_safe(work, COL_DEMAND)).fillna(0)
    work["_fert_oh"] = _num(_safe(work, COL_FERT_OH)).fillna(0)
    work["_raw_oh"] = _num(_safe(work, COL_RAW_OH)).fillna(0)
    work["_corp_oh"] = _num(_safe(work, COL_CORP_OH)).fillna(0)
    work["_oo"] = _num(_safe(work, COL_OO)).fillna(0)
    work["_urgent"] = urgent_mask(work).astype(int)

    grouped = work.groupby(work[key].fillna("(blank)").astype(str).str.strip(), dropna=False)
    out = grouped.agg(
        Rows=("_demand", "size"),
        Demand=("_demand", "sum"),
        **{
            "Fert OH": ("_fert_oh", "sum"),
            "Raw OH": ("_raw_oh", "sum"),
            "Corp OH": ("_corp_oh", "sum"),
            "On Order": ("_oo", "sum"),
            "Action Items": ("_urgent", "sum"),
        },
    ).reset_index()
    out = out.rename(columns={key: "Group"})
    out = out.sort_values(["Action Items", "Demand"], ascending=[False, False])
    return out.reset_index(drop=True)


def build_by_abc(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["_abc"] = _abc(work)
    work["_demand"] = _num(_safe(work, COL_DEMAND)).fillna(0)
    work["_corp_oh"] = _num(_safe(work, COL_CORP_OH)).fillna(0)
    work["_oo"] = _num(_safe(work, COL_OO)).fillna(0)
    work["_urgent"] = urgent_mask(work).astype(int)
    out = work.groupby("_abc", dropna=False).agg(
        Rows=("_demand", "size"),
        Demand=("_demand", "sum"),
        **{
            "Corp OH": ("_corp_oh", "sum"),
            "On Order": ("_oo", "sum"),
            "Action Items": ("_urgent", "sum"),
        },
    ).reset_index().rename(columns={"_abc": "ABC"})
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    out["_order"] = out["ABC"].map(order).fillna(99)
    return out.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def build_by_vendor(df: pd.DataFrame, top_n: int = 25) -> pd.DataFrame:
    out = _group_summary(df, COL_VENDOR)
    if out.empty:
        return out
    return out.head(top_n)


def build_by_plant(df: pd.DataFrame) -> pd.DataFrame:
    out = _group_summary(df, COL_PLANT)
    if out.empty:
        return out
    def _sort_key(v):
        try:
            return (0, int(float(v)))
        except (TypeError, ValueError):
            return (1, str(v))
    out = out.sort_values("Group", key=lambda s: s.map(_sort_key))
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Excel writing
# ---------------------------------------------------------------------------
def _style_sheet(ws, header_fill=BLUE, freeze="A2"):
    thin = Side(style="thin", color="FF000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in ws[1]:
        cell.fill = PatternFill(start_color=header_fill, end_color=header_fill, fill_type="solid")
        cell.font = Font(name=FONT_NAME, size=FONT_SIZE, bold=True, color="FF000000")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name=FONT_NAME, size=FONT_SIZE)
            cell.border = border

    # Column widths sized to content (bounded).
    for col_idx, col_cells in enumerate(ws.columns, start=1):
        width = 10
        for c in col_cells:
            if c.value is not None:
                width = max(width, min(len(str(c.value)) + 2, 48))
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    if freeze:
        ws.freeze_panes = freeze


def write_workbook(sheets: dict, out_path: Path):
    """Write {sheet_name: DataFrame} to an xlsx, one sheet per entry."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    wb = load_workbook(out_path)
    for name in sheets:
        if name in wb.sheetnames:
            _style_sheet(wb[name])

    # Numeric columns: give them a plain integer-ish format where it helps.
    for name in ("Action Items", "By ABC", "By Vendor", "By Plant"):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        header = [c.value for c in ws[1]]
        numeric_cols = {
            "Total Demand", "1mth", "ADCR", "Fert OH", "Raw OH",
            "Current Corp OH ", "Current OO", "DOS OH", "DOS OO",
            "Demand", "Corp OH", "On Order", "Action Items", "Rows",
        }
        for i, h in enumerate(header, start=1):
            if h in numeric_cols:
                for row in range(2, ws.max_row + 1):
                    ws.cell(row=row, column=i).number_format = "#,##0"

    wb.save(out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_report(source: Path, output: Path | None = None, timestamp=True) -> Path:
    df = load_master(source)

    sheets = {
        "Overview": build_overview(source, df),
        "Action Items": build_action_items(df),
        "By ABC": build_by_abc(df),
        "By Vendor": build_by_vendor(df),
        "By Plant": build_by_plant(df),
        "Data": df,
    }

    if output is None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "_", source.stem)
        suffix = f"_{datetime.now():%Y%m%d_%H%M%S}" if timestamp else ""
        output = REPORT_DIR / f"Report_{stem}{suffix}.xlsx"

    write_workbook(sheets, output)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone summary report (Excel) from an ACR Master File. "
                    "Independent of the action-code automation.",
    )
    parser.add_argument(
        "master_file", nargs="?",
        help="Path to a Master_File_Action_Code*.xlsx. Defaults to the newest one in outputs/.",
    )
    parser.add_argument(
        "-o", "--output", help="Output .xlsx path. Defaults to outputs/reports/Report_<source>_<stamp>.xlsx",
    )
    parser.add_argument(
        "--no-timestamp", action="store_true",
        help="Omit the timestamp from the default output filename.",
    )
    args = parser.parse_args(argv)

    try:
        source = Path(args.master_file) if args.master_file else find_latest_master_file()
        out = build_report(source, Path(args.output) if args.output else None, timestamp=not args.no_timestamp)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Report a short summary to stdout (useful when run from a scheduler/log).
    print(f"Report written: {out}")
    try:
        overview = pd.read_excel(out, sheet_name="Overview")
        for _, row in overview.iterrows():
            metric = row["Metric"]
            if pd.notna(metric) and str(metric).strip():
                print(f"  {metric}: {row['Value']}")
    except Exception:  # noqa: BLE001 - summary readout is best-effort
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
