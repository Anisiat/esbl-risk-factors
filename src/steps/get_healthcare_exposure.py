import numpy as np 
import pandas as pd
import logging
from pathlib import Path
import sys
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / 'data'
INTERIM_DIR = DATA_DIR / 'interim'

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import data_cleaning_tools as dct

cfg = dct.load_config(PROJECT_ROOT / 'configs' / 'config.yaml')

def get_healthcare_exposure(
    df,
    cfg_paths,
    exposure_type="any",
    index_cols=("subject", "admission_date"),
    window_days=90,
):
    """
    Add binary healthcare exposure features in the window before admission.

    Supported exposure types
    ------------------------
    emergency:
        Prior emergency attendance within the lookback window.

    inpatient:
        Prior inpatient admission overlapping the lookback window.

    outpatient:
        Prior outpatient appointment within the lookback window.

    any:
        Derives all three exposure variables and a combined
        any_healthcare_exposure_<window>d feature.

    Parameters
    ----------
    df : pd.DataFrame
        Main analysis dataframe.

    cfg_paths : dict
        Configuration dictionary containing paths to the exposure datasets.

    exposure_type : {"emergency", "hospital", "outpatient", "any"}
        Exposure feature(s) to derive.

    index_cols : tuple[str, str]
        Subject identifier and index admission-date columns.

    window_days : int
        Lookback window before admission, in days.

    Returns
    -------
    pd.DataFrame
        Input dataframe with healthcare exposure features added.
    """

    df = df.copy()

    subject_col, admission_col = index_cols

    df[admission_col] = pd.to_datetime(
        df[admission_col],
        errors="coerce",
    )

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _load_exposure(path_key):
        exposure_path = PROJECT_ROOT / cfg_paths[path_key]

        exposure_raw = pd.read_csv(
            exposure_path,
            low_memory=False,
        )

        exposure_df = dct.clean_df_columns(exposure_raw)

        return exposure_df

    def _flag_prior_window(
        exposure_df,
        start_col,
        output_col,
        end_col=None,
    ):
        """
        Return one binary exposure flag per subject + index admission.

        For point events:
            start_col must occur within the lookback window.

        For interval exposures:
            the interval must overlap the lookback window.
        """

        exposure_df = exposure_df.copy()

        # Convert exposure dates to datetime
        exposure_df[start_col] = pd.to_datetime(exposure_df[start_col], errors="coerce")

        if end_col is not None:
            exposure_df[end_col] = pd.to_datetime(exposure_df[end_col], errors="coerce")

        # Take the index admissions we are evaluating
        # (one row per subject + admission date)
        tmp = df[[subject_col, admission_col]].drop_duplicates().copy()

        # Keep only the columns needed for matching exposure records
        exposure_cols = [subject_col, start_col]
        if end_col is not None:
            exposure_cols.append(end_col)

        # Join the index admissions to exposure records for the same subject
        merged = tmp.merge(
            exposure_df[exposure_cols].drop_duplicates(),
            on=subject_col,
            how="left",
        )

        # Define the start of the lookback window
        window_start = merged[admission_col] - pd.Timedelta(days=window_days)

        # For point events: event date must be before admission and within the window
        if end_col is None:
            mask = (
                merged[start_col].notna()
                & (merged[start_col] < merged[admission_col])
                & (merged[start_col] >= window_start)
            )

        # For interval events: exposure interval must overlap the window
        else:
            effective_end = merged[end_col].fillna(merged[start_col])

            mask = (
                merged[start_col].notna()
                & (merged[start_col] < merged[admission_col])
                & (effective_end >= window_start)
            )

        # Add the flag column and collapse to one row per admission
        # using max() so that any qualifying event gives 1
        flags = (
            merged.assign(**{output_col: mask.astype(int)})
            .groupby([subject_col, admission_col], as_index=False)[output_col]
            .max()
        )

        return flags

    # ---------------------------------------------------------
    # Exposure extraction
    # ---------------------------------------------------------

    exposure_flags = []

    if exposure_type in ("emergency", "any"):
        emergency_df = _load_exposure(
            "emergency_exposure"
        )

        emergency_flags = _flag_prior_window(
            emergency_df,
            start_col="arrival_date",
            output_col=f"emergency_exposure_{window_days}d",
        )

        exposure_flags.append(
            emergency_flags
        )

    if exposure_type in ("inpatient", "any"):
        hospital_df = _load_exposure(
            "inpatient_exposure"
        )

        hospital_flags = _flag_prior_window(
            hospital_df,
            start_col="prior_admission_date",
            end_col="prior_discharge_date",
            output_col=f"inpatient_exposure_{window_days}d",
        )

        exposure_flags.append(
            hospital_flags
        )

    if exposure_type in ("outpatient", "any"):
        outpatient_df = _load_exposure(
            "outpatient_exposure"
        )

        outpatient_flags = _flag_prior_window(
            outpatient_df,
            start_col="appointment_date",
            output_col=f"outpatient_exposure_{window_days}d",
        )

        exposure_flags.append(
            outpatient_flags
        )

    valid_types = {
        "emergency",
        "hospital",
        "outpatient",
        "any",
    }

    if exposure_type not in valid_types:
        raise ValueError(
            "exposure_type must be one of: "
            "'emergency', 'hospital', 'outpatient', 'any'"
        )

    # ---------------------------------------------------------
    # Merge exposure flags onto analysis dataframe
    # ---------------------------------------------------------

    for flags in exposure_flags:
        df = df.merge(
            flags,
            on=[subject_col, admission_col],
            how="left",
        )

    exposure_cols = [
        f"emergency_exposure_{window_days}d",
        f"hospital_exposure_{window_days}d",
        f"outpatient_exposure_{window_days}d",
    ]

    for col in exposure_cols:
        if col in df.columns:
            df[col] = (
                df[col]
                .fillna(0)
                .astype(int)
            )

    # ---------------------------------------------------------
    # Combined healthcare exposure
    # ---------------------------------------------------------

    if exposure_type == "any":

        df[f"any_healthcare_exposure_{window_days}d"] = (
            df[
                [
                    f"emergency_exposure_{window_days}d",
                    f"inpatient_exposure_{window_days}d",
                    # f"outpatient_exposure_{window_days}d",
                ]
            ]
            .max(axis=1)
            .astype(int)
        )

    return df