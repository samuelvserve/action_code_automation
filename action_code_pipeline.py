"""
Action Code Report Automation
==============================
Automates: GAINS extraction -> Master File build -> Date of Supply enrichment
-> Total Demand pivot -> SAP MB52 (Fert OH, Raw OH) -> SAP ZEXP (Current OO)
-> derived columns -> final formatted Excel.

All join keys and formulas below were reverse-verified against a real
expected_masterfile.xlsx sample, not just the written SOP.
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

import pandas as pd
import numpy as np
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
INPUT_DIR = Path("./inputs")
OUTPUT_DIR = Path("./outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    "gains_raw":       INPUT_DIR / "AC12_hot_raw.xlsx",      # Step 1 GAINS download
    "date_of_supply":  INPUT_DIR / "Date_of_Supply.xlsx",    # Step 3 monthly file
    "total_demand":    INPUT_DIR / "Total_Demant.xlsx",      # GAINS pivot export
    "mb52_fert_oh":    INPUT_DIR / "MB52-Fert_oh.xlsx",      # Step 4 SAP export
    "mb52_raw_oh":     INPUT_DIR / "MB52-RO_oh.xlsx",        # Step 5 SAP export
    "zexp":            INPUT_DIR / "ZEXP.xlsx",              # Step 6 SAP export
}

MASTER_OUTPUT = OUTPUT_DIR / "Master_File_Action_Code.xlsx"

EXCLUDED_LOCATIONS = [2006, 2011]
# Purchasing codes supported by the web UI. The GAINS raw export may contain
# one or more of these; a run filters to the code(s) the user selected.
PURCHASING_GROUPS = ["D07", "D09", "D13", "D17", "D25", "D20", "D40", "D16", "D30", "S18", "S02", "S04"]
DEFAULT_PURCHASING_GROUP = "D13"
MATERIAL_TYPE = "FERT"

# Exact Master File column layout (matches template + expected output,
# including the leading/trailing spaces in " PDT" and "Current Corp OH ")
MASTER_COLUMNS = [
    "Fert#", "Ord Mat'l", "Plant", "Material Description",
    "Vendor number", "Vendor name", "Action Code", "ABC",
    " PDT", "Total Demand", "1mth", "ADCR",
    "Fert OH", "Raw OH", "Current Corp OH ", "Current OO",
    "DOS OH", "DOS OO",
]


def normalize_id(series: pd.Series) -> pd.Series:
    """Material/Item IDs come in mixed forms across sources (int, float-like
    '10123.0', or alphanumeric like '100M01'). Normalize all to plain
    strings without forcing numeric casts, which breaks on alnum values."""
    s = series.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    return s


# ---------------------------------------------------------------------------
# STEP 1: GAINS extraction + cleaning
# ---------------------------------------------------------------------------
def load_and_clean_gains(path: Path, purchasing_groups: Optional[List[str]] = None) -> pd.DataFrame:
    """AC12 hot raw columns: *Item, *Location, *Action Code, On Hand,
    *Total Dmd, Purchasing Group, *Total EROs, Delivery Unit,
    Inv Class (Lawson), Material Type.

    purchasing_groups: list of codes to keep (e.g. ["D13", "D17"]). Defaults
    to the single configured default code.
    """
    groups = purchasing_groups or [DEFAULT_PURCHASING_GROUP]
    df = pd.read_excel(path, sheet_name=0)
    df.columns = [str(c).lstrip("*").strip() for c in df.columns]

    # NOTE: 'Item' is intentionally excluded here - it's a mixed
    # numeric/alphanumeric material ID (e.g. 'FA5710M05', 'KT14362'),
    # and pd.to_numeric(errors="coerce") would silently wipe those to NaN.
    for col in ["Location", "On Hand", "Total Dmd", "Total EROs"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Verified against expected_masterfile.xlsx: the AC12 hot raw export
    # is already scoped to Purchasing Group D13 upstream in the GAINS
    # query itself, and legitimately includes both FERT and HALB material
    # types plus rows at Location 2006 that should stay. Applying the
    # written SOP's manual Material Type / Location filters here would
    # silently DROP valid rows, so only the purchasing-group filter is
    # kept as a safety net for raw exports that weren't pre-scoped.
    if "Purchasing Group" in df.columns:
        df = df[df["Purchasing Group"].astype(str).str.strip().isin(groups)]
    else:
        logging.warning("No 'Purchasing Group' column in %s - skipping filter", path)

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# STEP 2: Map raw AC12 hot -> Master File (one row per Item+Location, no dedup)
# ---------------------------------------------------------------------------
def build_master_from_gains(gains_df: pd.DataFrame) -> pd.DataFrame:
    cols = {
        "Fert#": normalize_id(gains_df["Item"]),
        "Plant": gains_df["Location"],
        "Action Code": gains_df["Action Code"],
        "ABC": gains_df["Inv Class (Lawson)"],
    }
    # Kept through the merge chain for per-purchasing-code stats, then
    # dropped before the final MASTER_COLUMNS selection so it never
    # appears in the Excel output.
    if "Purchasing Group" in gains_df.columns:
        cols["Purchasing Group"] = gains_df["Purchasing Group"]
    return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# STEP 3: Date of Supply enrichment (Ord Mat'l, Vendor, Description, PDT)
# ---------------------------------------------------------------------------
def enrich_with_date_of_supply(master: pd.DataFrame, dos_path: Path) -> pd.DataFrame:
    dos = pd.read_excel(dos_path)
    dos["Material"] = normalize_id(dos["Material"])

    dos_lookup = dos.rename(columns={
        "Component Material": "Ord Mat'l",
        "Primary US Vendor Num": "Vendor number",
        "Primary US Vendor": "Vendor name",
    })[["Material", "Ord Mat'l", "Material Description",
        "Vendor number", "Vendor name"]]
    dos_lookup["Ord Mat'l"] = normalize_id(dos_lookup["Ord Mat'l"])

    master = master.merge(dos_lookup, left_on="Fert#", right_on="Material", how="left")
    master.drop(columns=["Material"], inplace=True)
    master["Vendor number"] = master["Vendor number"].fillna(0)

    # ' PDT' is verified (against expected_masterfile.xlsx) to always be
    # left blank - it's a manual buyer-filled field, not auto-populated
    # despite DOS having a 'Max of Planned Delivery Time' column.
    master[" PDT"] = np.nan
    return master


# ---------------------------------------------------------------------------
# STEP 3b: Total Demand (GAINS pivot export - "Sum of *Total Dmd" by Item)
# ---------------------------------------------------------------------------
def enrich_with_total_demand(master: pd.DataFrame, td_path: Path) -> pd.DataFrame:
    # Pivot export: real header row is row index 6 (0-indexed), Item column
    # is '*Item', and grand total across all plants is 'Total Result'.
    td = pd.read_excel(td_path, header=6)
    td.columns = [str(c).lstrip("*").strip() for c in td.columns]
    td["Item"] = normalize_id(td["Item"])

    td_lookup = td[["Item", "Total Result"]].rename(columns={"Total Result": "Total Demand"})
    master = master.merge(td_lookup, left_on="Fert#", right_on="Item", how="left")
    master.drop(columns=["Item"], inplace=True)
    master["Total Demand"] = master["Total Demand"].fillna(0)
    return master


# ---------------------------------------------------------------------------
# STEP 4 & 5: SAP MB52 (Fert OH / Raw OH) - sum Unrestricted across all plants
# ---------------------------------------------------------------------------
def pivot_mb52(path: Path) -> pd.Series:
    df = pd.read_excel(path)
    df["Material"] = normalize_id(df["Material"])
    df["Unrestricted"] = pd.to_numeric(df["Unrestricted"], errors="coerce")
    return df.groupby("Material")["Unrestricted"].sum()


def merge_mb52_oh(master: pd.DataFrame, files: dict) -> pd.DataFrame:
    fert_oh = pivot_mb52(files["mb52_fert_oh"])
    raw_oh = pivot_mb52(files["mb52_raw_oh"])

    master["Fert OH"] = master["Fert#"].map(fert_oh).fillna(0)
    master["Raw OH"] = master["Ord Mat'l"].map(raw_oh).fillna(0)
    return master


# ---------------------------------------------------------------------------
# STEP 6: SAP ZEXP (Current On Order) - sum "To be del." by material
# ---------------------------------------------------------------------------
def merge_zexp_oo(master: pd.DataFrame, files: dict) -> pd.DataFrame:
    df = pd.read_excel(files["zexp"])
    df["Material"] = normalize_id(df["Material"])
    df["To be del."] = pd.to_numeric(df["To be del."], errors="coerce")
    pivot = df.groupby("Material")["To be del."].sum()

    master["Current OO"] = master["Ord Mat'l"].map(pivot).fillna(0)
    return master


# ---------------------------------------------------------------------------
# STEP 6b: Derived columns (verified against expected_masterfile.xlsx)
# 1mth = Total Demand / 12   |   ADCR = Total Demand / 252 (working days)
# Current Corp OH = Fert OH + Raw OH
# DOS OH = Current Corp OH / ADCR   |   DOS OO = Current OO / ADCR
# ---------------------------------------------------------------------------
def compute_derived_columns(master: pd.DataFrame) -> pd.DataFrame:
    master["1mth"] = (master["Total Demand"] / 12).round(0)

    # Keep a full-precision ADCR internally for the DOS OH/OO division
    # (ADCR is usually < 1, so rounding it first would zero out the
    # divisor and break DOS OH/OO). The ADCR column itself is rounded
    # per request - it's fine if it displays as 0.
    adcr_raw = master["Total Demand"] / 252
    master["ADCR"] = adcr_raw.round(0)

    master["Current Corp OH "] = master["Fert OH"].fillna(0) + master["Raw OH"].fillna(0)

    master["DOS OH"] = np.where(
        adcr_raw > 0, (master["Current Corp OH "] / adcr_raw).round(0), np.nan
    )
    master["DOS OO"] = np.where(
        adcr_raw > 0, (master["Current OO"] / adcr_raw).round(0), np.nan
    )
    return master


# ---------------------------------------------------------------------------
# STEP 7: Formatted output matching Master File template styling
# ---------------------------------------------------------------------------
def write_formatted_excel(master: pd.DataFrame, out_path: Path):
    master.to_excel(out_path, index=False, sheet_name="Master File")

    wb = load_workbook(out_path)
    ws = wb["Master File"]

    # --- exact styling reverse-engineered from expected_masterfile.xlsx ---
    BLUE = "FF83CBEB"
    ORANGE = "FFF2AA84"
    RED_FONT = "FFFF0000"
    PURPLE_FONT = "FF78206E"
    BLACK_FONT = "FF000000"

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    font_name, font_size = "Aptos Narrow", 11

    # header_fill, header_font_color, body_number_format, body_fill (or None)
    COLUMN_STYLE = {
        "Fert#":                (BLUE,   RED_FONT,    "General", None),
        "Ord Mat'l":            (ORANGE, BLACK_FONT,  "General", None),
        "Plant":                (BLUE,   RED_FONT,    "General", None),
        "Material Description": (ORANGE, BLACK_FONT,  "General", None),
        "Vendor number":        (ORANGE, BLACK_FONT,  "General", None),
        "Vendor name":          (ORANGE, BLACK_FONT,  "General", None),
        "Action Code":          (BLUE,   RED_FONT,    "0",       None),
        "ABC":                  (BLUE,   RED_FONT,    "@",       None),
        " PDT":                 (BLUE,   BLACK_FONT,  "General", None),
        "Total Demand":         (ORANGE, BLACK_FONT,  "0",       None),
        "1mth":                 (BLUE,   BLACK_FONT,  "0",       None),
        "ADCR":                 (BLUE,   BLACK_FONT,  "0",       None),
        "Fert OH":              (BLUE,   PURPLE_FONT, "General", None),
        "Raw OH":               (BLUE,   PURPLE_FONT, "General", None),
        "Current Corp OH ":     (BLUE,   BLACK_FONT,  "General", None),
        "Current OO":           (BLUE,   PURPLE_FONT, "General", None),
        "DOS OH":               (BLUE,   BLACK_FONT,  "0",       None),
        "DOS OO":               (BLUE,   BLACK_FONT,  "0",       "FFFFFFFF"),
    }

    # --- header row ---
    ws.row_dimensions[1].height = 26.5
    for col_idx, col_name in enumerate(master.columns, start=1):
        fill_color, font_color, _, _ = COLUMN_STYLE[col_name]
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
        cell.font = Font(name=font_name, size=font_size, bold=True, color=font_color)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    CENTER_ALIGN_COLUMNS = {
        "Fert#", "Ord Mat'l", "Plant", "Vendor number", "Action Code", "ABC",
        " PDT", "Total Demand", "1mth", "ADCR", "Fert OH", "Raw OH",
        "Current Corp OH ", "Current OO", "DOS OH", "DOS OO",
    }

    # --- body rows ---
    dos_oh_col_idx = list(master.columns).index("DOS OH") + 1
    abc_col_idx = list(master.columns).index("ABC") + 1
    action_col_idx = list(master.columns).index("Action Code") + 1

    RED_FILL = PatternFill(start_color="FFFF0000", end_color="FFFF0000", fill_type="solid")
    WHITE_FONT = "FFFFFFFF"

    for row_idx in range(2, ws.max_row + 1):
        ws.row_dimensions[row_idx].height = 13.85

        abc_val = ws.cell(row=row_idx, column=abc_col_idx).value
        action_val = ws.cell(row=row_idx, column=action_col_idx).value
        dos_oh_val = ws.cell(row=row_idx, column=dos_oh_col_idx).value

        try:
            dos_oh_num = float(dos_oh_val) if dos_oh_val is not None else None
        except (TypeError, ValueError):
            dos_oh_num = None

        dos_oh_under_21 = dos_oh_num is not None and dos_oh_num < 21
        row_should_bold = (
            str(abc_val).strip() in ("A", "B")
            and action_val in (1, 2)
            and dos_oh_under_21
        )

        for col_idx, col_name in enumerate(master.columns, start=1):
            _, header_font_color, num_fmt, body_fill = COLUMN_STYLE[col_name]
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.number_format = num_fmt
            cell.border = border
            if col_name in CENTER_ALIGN_COLUMNS:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            if body_fill:
                cell.fill = PatternFill(start_color=body_fill, end_color=body_fill, fill_type="solid")

            # DOS OH cell: red fill + white text whenever DOS OH < 21
            if col_name == "DOS OH" and dos_oh_under_21:
                cell.fill = RED_FILL
                cell.font = Font(name=font_name, size=font_size, bold=row_should_bold, color=WHITE_FONT)
            else:
                cell.font = Font(name=font_name, size=font_size, bold=row_should_bold)

    wb.save(out_path)


# ---------------------------------------------------------------------------
# STATS (mirrors the Excel highlight rule: A/B class + Action Code 1/2 +
# DOS OH < 21 days = urgent expedite candidates)
# ---------------------------------------------------------------------------
def _action_mask(master: pd.DataFrame) -> pd.Series:
    abc = master["ABC"].astype(str).str.strip()
    action_code = pd.to_numeric(master["Action Code"], errors="coerce")
    dos_oh = pd.to_numeric(master["DOS OH"], errors="coerce")
    return abc.isin(["A", "B"]) & action_code.isin([1, 2]) & (dos_oh < 21)


def compute_per_code_stats(master: pd.DataFrame) -> dict:
    """Rows + action items split by Purchasing Group (for the dashboard)."""
    if "Purchasing Group" not in master.columns:
        return {}
    mask = _action_mask(master)
    out = {}
    for code, sub in master.groupby(master["Purchasing Group"].astype(str).str.strip()):
        if not code:
            continue
        out[code] = {
            "rows": int(len(sub)),
            "action_items": int(mask.loc[sub.index].sum()),
        }
    return out


class PipelineCancelled(Exception):
    """Raised between pipeline steps when a user stops the run."""


def compute_stats(master: pd.DataFrame) -> dict:
    """Aggregate metrics for the dashboard from a finished Master File."""
    total = len(master)
    if total == 0:
        return {
            "total_rows": 0, "action_items": 0, "abc_a": 0, "abc_b": 0,
            "abc_c": 0, "abc_d": 0, "total_demand": 0, "at_risk": 0,
            "purchasing_groups": [],
        }

    abc = master["ABC"].astype(str).str.strip()
    at_risk = int(_action_mask(master).sum())

    return {
        "total_rows": int(total),
        "action_items": int(at_risk),
        "abc_a": int((abc == "A").sum()),
        "abc_b": int((abc == "B").sum()),
        "abc_c": int((abc == "C").sum()),
        "abc_d": int((abc == "D").sum()),
        "total_demand": float(master["Total Demand"].sum()),
        "at_risk": int(at_risk),
        "purchasing_groups": sorted(
            master.get("Purchasing Group", pd.Series(dtype=str))
            .astype(str).str.strip().unique().tolist()
        ) if "Purchasing Group" in master.columns else [],
    }


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------
def run_pipeline(
    purchasing_groups: Optional[List[str]] = None,
    input_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    output_name: Optional[str] = None,
    should_cancel: Optional[callable] = None,
    on_step: Optional[callable] = None,
) -> dict:
    """Run the full pipeline for the selected purchasing code(s).

    should_cancel: optional callback checked between steps; when it returns
    True a PipelineCancelled is raised so the caller can stop cleanly.

    on_step: optional callback called with a human-readable description of
    each step as it starts (used by the UI to show live progress).

    Returns a stats dict (see compute_stats) plus 'output_path'.
    """
    started = time.time()
    groups = purchasing_groups or [DEFAULT_PURCHASING_GROUP]

    def check_cancel():
        if should_cancel and should_cancel():
            raise PipelineCancelled("Run stopped by user")

    def step(text: str):
        print(text)
        if on_step:
            try:
                on_step(text)
            except Exception:  # noqa: BLE001 - progress reporting must never break the run
                pass

    files = {k: (input_dir or INPUT_DIR) / v.name for k, v in FILES.items()}
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if output_name is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"Master_File_Action_Code_{'_'.join(groups)}_{stamp}.xlsx"
    master_output = out_dir / output_name

    logging.info("Run started: purchasing_groups=%s", groups)
    check_cancel()
    step(f"Step 1-2: Loading & cleaning GAINS data (groups={groups}), building Master File...")
    gains = load_and_clean_gains(files["gains_raw"], groups)
    master = build_master_from_gains(gains)
    check_cancel()

    step("Step 3: Enriching with Date of Supply (Ord Mat'l, Vendor, PDT)...")
    master = enrich_with_date_of_supply(master, files["date_of_supply"])
    check_cancel()

    step("Step 3b: Enriching with Total Demand pivot...")
    master = enrich_with_total_demand(master, files["total_demand"])
    check_cancel()

    step("Step 4-5: Merging SAP MB52 Fert OH / Raw OH...")
    master = merge_mb52_oh(master, files)
    check_cancel()

    step("Step 6: Merging SAP ZEXP Current OO...")
    master = merge_zexp_oo(master, files)
    check_cancel()

    step("Step 6b: Computing derived columns (1mth, ADCR, Current Corp OH, DOS OH/OO)...")
    master = compute_derived_columns(master)
    check_cancel()

    # Per-code stats BEFORE dropping the Purchasing Group column.
    stats = compute_stats(master)
    stats["by_code"] = compute_per_code_stats(master)

    master = master[MASTER_COLUMNS]
    check_cancel()

    step("Step 7: Writing formatted Master File for buyer review...")
    write_formatted_excel(master, master_output)

    stats["output_path"] = str(master_output)
    stats["duration_sec"] = round(time.time() - started, 1)
    stats["purchasing_groups"] = groups

    logging.info(
        "Run finished: rows=%s action_items=%s output=%s",
        stats["total_rows"], stats["action_items"], master_output.name,
    )
    print(f"Done. Output: {master_output} ({len(master)} rows)")
    return stats


if __name__ == "__main__":
    run_pipeline()