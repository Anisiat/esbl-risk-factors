"""Analyse treatment function code (TFC) by mapped hospital site.

The script reads a CSV file containing at least the columns ``surgical_area`` and
``tfc``, maps each surgical area to a hospital site category, and saves a
cross-tabulation of TFC by hospital site to a user-specified output path.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"surgical_area", "tfc"}


def map_hospital_site(value):
    """Map a raw surgical area value to a standard hospital site label."""
    if pd.isna(value):
        return np.nan

    value = str(value).lower().strip()

    # Single-site records
    if value.startswith("hh"):
        return "HH"

    if value.startswith("cx"):
        return "CX"

    if value.startswith("sm"):
        return "SM"

    # Other locations — keep explicit for checking
    if value.startswith("we"):
        return "WE"

    if value.startswith("qc"):
        return "QC"

    if value == "none":
        return np.nan

    # Multiple sites recorded
    if "|" in value:
        return "MULTIPLE"

    return "OTHER"


def load_data(file_path):
    """Load a CSV file and validate that the required columns exist."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"Input data is missing required columns: {missing_list}")

    return df


def analyse_hosp_tfc(df):
    """Return a cross-tabulation of TFC by mapped hospital site."""
    df = df.copy()

    df["hospital_site"] = (
        df["surgical_area"]
        .apply(map_hospital_site)
    )

    summary = pd.crosstab(
        df["tfc"],
        df["hospital_site"],
        dropna=False,
    )

    return summary


def parse_args():
    """Parse the input and output file paths from the command line."""
    parser = argparse.ArgumentParser(
        description="Analyse treatment function code by hospital site."
    )
    parser.add_argument(
        "input_csv",
        type=str,
        help="Path to the CSV file containing surgical_area and tfc columns.",
    )
    parser.add_argument(
        "output_csv",
        type=str,
        help="Path where the TFC-by-site summary CSV should be saved.",
    )
    return parser.parse_args()


def main():
    """Run the analysis from the terminal and save the output."""
    args = parse_args()
    df = load_data(args.input_csv)
    summary = analyse_hosp_tfc(df)

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_path)

    print(f"Saved TFC-by-site summary to: {output_path}")
    print(summary)


if __name__ == "__main__":
    main()