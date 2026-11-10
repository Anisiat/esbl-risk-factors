"""Summarise in-hospital mortality and length of stay by ESBL status.

This script loads a single CSV file, converts ESBL status into a binary flag,
aggregates the data to admission level, and prints a summary comparing outcomes
for admissions with and without ESBL.
"""

import argparse
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "spell_identifier",
    "subject",
    "admission_date",
    "discharge_date",
    "esbl_status",
    "in_hosp_mortality",
    "los_days",
}


def load_data(file_path: str) -> pd.DataFrame:
    """Read a CSV file and validate that the expected columns are present."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    df = pd.read_csv(path)
    df.columns = [str(col).strip().lower() for col in df.columns]

    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(sorted(missing))
        )

    return df


def prepare_esbl_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Convert ESBL status to a binary variable for analysis."""
    df = df.copy()

    if pd.api.types.is_numeric_dtype(df["esbl_status"]):
        df["esbl_status"] = df["esbl_status"].astype(int)
    else:
        df["esbl_status"] = df["esbl_status"].eq("ESBL").astype(int)

    return df


def build_admission_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate rows to one record per admission and summarise outcomes."""
    admission_outcomes = (
        df
        .groupby("spell_identifier", as_index=False)
        .agg(
            subject=("subject", "first"),
            admission_date=("admission_date", "first"),
            discharge_date=("discharge_date", "first"),
            any_esbl=("esbl_status", "max"),
            in_hosp_mortality=("in_hosp_mortality", "max"),
            los_days=("los_days", "first"),
            n_infection_episodes=("esbl_status", "size"),
        )
    )

    summary = (
        admission_outcomes
        .groupby("any_esbl")
        .agg(
            admissions=("spell_identifier", "nunique"),
            mortality_n=("in_hosp_mortality", "sum"),
            mortality_prop=("in_hosp_mortality", "mean"),
            median_los=("los_days", "median"),
            q1_los=("los_days", lambda x: x.quantile(0.25)),
            q3_los=("los_days", lambda x: x.quantile(0.75)),
        )
        .reset_index()
    )

    summary["mortality_pct"] = 100 * summary["mortality_prop"]
    summary["any_esbl"] = summary["any_esbl"].map({0: "No ESBL", 1: "ESBL"})
    return summary


def parse_args():
    """Parse the filepath argument from the command line."""
    parser = argparse.ArgumentParser(
        description="Summarise LOS and in-hospital mortality by ESBL status."
    )
    parser.add_argument(
        "filepath",
        type=str,
        help="Path to the CSV file you want to analyse.",
    )
    return parser.parse_args()


def main():
    """Run the analysis for a single CSV file provided by the user."""
    args = parse_args()
    analysis_df = load_data(args.filepath)
    analysis_df = prepare_esbl_flags(analysis_df)
    summary = build_admission_summary(analysis_df)
    print(summary)


if __name__ == "__main__":
    main()