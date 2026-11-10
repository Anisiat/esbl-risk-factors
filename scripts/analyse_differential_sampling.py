"""Summarise postoperative microbiology sampling by surgical specialty.

This script can read surgery spell data from a directory of CSV files, convert
it to a per-admission summary, and count postoperative microbiology samples by
specialty.
"""

import argparse
from pathlib import Path

import pandas as pd


# Date columns need to be converted to datetime objects so that we can compare
# them reliably and derive postoperative sampling windows.
date_cols = [
    "LATEST_COLLECT_DT",
    "SURGERY_START_DT",
    "SURGERY_STOP_DT",
    "ADMISSION_DATE",
    "DISCHARGE_DATE",
]


def load_surgery_data(file_path: str) -> pd.DataFrame:
    """Load a single CSV file containing surgery spell data."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    return pd.read_csv(path)


def parse_args():
    """Parse the single CSV filepath argument."""
    parser = argparse.ArgumentParser(
        description="Summarise postoperative microbiology sampling by specialty."
    )
    parser.add_argument(
        "filepath",
        type=str,
        help="Path to the CSV file containing surgery data.",
    )
    return parser.parse_args()


def main():
    """Build a specialty-level summary of postoperative microbiology sampling.

    The analysis flags any microbiology test collected after surgery start but
    before discharge, then groups the results to the spell/admission level before
    calculating summary measures by specialty.
    """
    args = parse_args()
    all_surgeries = load_surgery_data(args.filepath)

    for col in date_cols:
        all_surgeries[col] = pd.to_datetime(
            all_surgeries[col],
            errors="coerce"
        )

    # A postoperative sample is any microbiology result taken after the surgery
    # start time and before the patient is discharged.
    all_surgeries["postop_sample"] = (
        all_surgeries["LATEST_COLLECT_DT"].notna()
        & (all_surgeries["LATEST_COLLECT_DT"] >= all_surgeries["SURGERY_START_DT"])
        & (all_surgeries["LATEST_COLLECT_DT"] <= all_surgeries["DISCHARGE_DATE"])
    )

    # Collapse the row-level data to one record per admission/spell. This keeps the
    # specialty context while summarising the number of postoperative samples and
    # the total inpatient stay associated with that spell.
    admission_level = (
        all_surgeries
        .groupby("SPELL_IDENTIFIER")
        .agg(
            subject=("SUBJECT", "first"),
            specialty=("TREATMENT_FUNCTION_CODE_DES", "first"),
            surgical_area=("SURGICAL_AREA", "first"),
            surgery_start=("SURGERY_START_DT", "first"),
            discharge_date=("DISCHARGE_DATE", "first"),

            # Count unique test IDs to avoid double-counting duplicate records
            # within the same spell when the same sample is repeated.
            n_postop_samples=(
                "LAB_TEST_ID",
                lambda x: x[all_surgeries.loc[x.index, "postop_sample"]].nunique()
            )
        )
        .reset_index()
    )

    # Convert the sample count into a binary flag for any postoperative sample.
    admission_level["any_postop_sample"] = (
        admission_level["n_postop_samples"] > 0
    )

    # Calculate length of stay in days from surgery start to discharge.
    admission_level["postop_inpatient_days"] = (
        admission_level["discharge_date"]
        - admission_level["surgery_start"]
    ).dt.total_seconds() / 86400

    # Aggregate to specialty level to compare the proportion of admissions with
    # at least one sample and the rate of sampling per patient-day.
    sampling_summary = (
        admission_level
        .groupby("specialty", dropna=False)
        .agg(
            elective_admissions=("SPELL_IDENTIFIER", "nunique"),
            admissions_with_postop_sample=("any_postop_sample", "sum"),
            total_postop_samples=("n_postop_samples", "sum"),
            total_postop_patient_days=("postop_inpatient_days", "sum"),
        )
        .reset_index()
    )

    sampling_summary["pct_admissions_sampled"] = (
        100
        * sampling_summary["admissions_with_postop_sample"]
        / sampling_summary["elective_admissions"]
    )

    sampling_summary["samples_per_100_patient_days"] = (
        100
        * sampling_summary["total_postop_samples"]
        / sampling_summary["total_postop_patient_days"]
    )

    # Sort by the share of admissions with a postoperative sample so the most
    # heavily sampled specialties appear first.
    sampling_summary = sampling_summary.sort_values(
        "pct_admissions_sampled",
        ascending=False
    )

    print(sampling_summary)


if __name__ == "__main__":
    main()