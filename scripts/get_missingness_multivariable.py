"""Run multivariable logistic regression for the ESBL analysis.

    python run_multivariable_lr.py  --cluster-by-patient --fit-mode full
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
# import statsmodels as sm
import statsmodels.api as sm
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import train_test_split, GroupShuffleSplit

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from utils import lr_helper_functions as lrh


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.yaml"
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "processed" / "lr_data.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "multivariable_lr"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multivariable logistic regression from LR-ready data."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config YAML. Defaults to configs/config.yaml.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="Path to lr_data.csv from run_univariable_lr.py.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for output CSV files. Defaults to outputs/multivariable_lr/.",
    )
    parser.add_argument(
        "--episodes",
        choices=["all", "first"],
        default="all",
        help="Use all infection episodes or only the first episode per subject.",
    )

    parser.add_argument(
        "--cluster-by-patient",
        action="store_true",
        help="Use patient-level cluster-robust standard errors.",
    )

    parser.add_argument(
        "--variables-file",
        type=Path,
        default= PROJECT_ROOT / 'outputs' / 'univariable_lr'/ 'all_episodes'/ 'cluster_by_patient' / 'variables_for_multivariable_lr.csv',
        help=(
            "Optional CSV with a predictor column. If supplied, fit only those "
            "predictors instead of all configured predictors."
        ),
    )
    parser.add_argument(
        "--correlation-threshold",
        type=float,
        default=0.7,
        help="Absolute correlation threshold for multicollinearity screening.",
    )
    parser.add_argument(
        "--vif-threshold",
        type=float,
        default=5.0,
        help="VIF threshold for flagging possible multicollinearity.",
    )

    parser.add_argument(
        "--fit-mode",
        choices= ['full', 'train-test'],
        default= 'full',
        help="Use all complete cases for model fitting or fit on a train split and evaluate on held-out test-set",
    )

    parser.add_argument(
        "--decision-threshold",
        type=float,
        default=0.5,
        help='probability threshold for test-set classification metrics,'

    )

    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(config_path: Path) -> dict:
    try:
        from utils import data_cleaning_tools as dct
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Could not import the repo config loader. Make sure the project "
            "environment is active and dependencies such as PyYAML are installed."
        ) from exc

    return dct.load_config(config_path)


def write_outputs(
    output_dir: Path,
    model_df: pd.DataFrame,
    results_df: pd.DataFrame,
    epv_df: pd.DataFrame,
    vif_df: pd.DataFrame,
    high_vif_df: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    high_corr_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    logistic_coefficients: pd.DataFrame
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    model_df.to_csv(output_dir / "multivariable_lr_model_data.csv", index=False)
    results_df.to_csv(output_dir / "multivariable_lr_results.csv", index=False)
    epv_df.to_csv(output_dir / "epv.csv", index=False)
    vif_df.to_csv(output_dir / "vif.csv", index=False)
    high_vif_df.to_csv(output_dir / "high_vif.csv", index=False)
    corr_matrix.to_csv(output_dir / "correlation_matrix.csv")
    high_corr_df.to_csv(output_dir / "high_correlation_pairs.csv", index=False)
    summary_df.to_csv(output_dir / "model_summary.csv", index=False)
    logistic_coefficients.to_csv(output_dir / 'logistic_coefficients.csv')

def validate_split_args(test_size:float, decision_threshold:float) ->None:
    if not 0< test_size <1:
        raise ValueError('--test-size must be between 0 and 1')

    if not 0< decision_threshold <1 :
        raise ValueError(
            '--decision-threshold must be between 0 and 1'
            )

def get_stratified_labels(y: pd.Series) -> pd.Series:
    'return y for stratified splitting if both outcome classes can be split'

    class_counts = y.value_counts(dropna = False)

    if len(class_counts) < 2 or class_counts.min() <2:
        logging.warning(
            'Train/test split will not be stratified bc an outcome clas has fewwer than two complete cases'
        )
        return None

    return y

def main() -> None:
    """Run the multivariable logistic regression workflow."""

    args = parse_args()

    config_path = resolve_path(args.config)
    input_path = resolve_path(args.input)
    output_dir = resolve_path(args.output_dir)

    variables_file = (
        resolve_path(args.variables_file)
        if args.variables_file
        else None
    )

    # ---------------------------------------------------------
    # Logging + config
    # ---------------------------------------------------------

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logging.info("Loading config from %s", config_path)

    cfg = load_config(config_path)
    lr_config = cfg["logistic_regression"]

    # ---------------------------------------------------------
    # Load data
    # ---------------------------------------------------------

    logging.info(
        "Loading LR-ready data from %s",
        input_path,
    )

    lr_df = pd.read_csv(
        input_path,
        low_memory=False,
    )

    # ---------------------------------------------------------
    # Episode restriction
    # ---------------------------------------------------------

    if args.episodes == "first":

        lr_df = lrh.restrict_to_first_episode(
            lr_df
        )

        output_dir = (
            output_dir
            / "first_episodes"
        )

    else:

        output_dir = (
            output_dir
            / "all_episodes"
        )

    # ---------------------------------------------------------
    # Clustering settings
    # ---------------------------------------------------------

    if (
        args.cluster_by_patient
        and args.episodes == "first"
    ):
        raise ValueError(
            "Do not use --cluster-by-patient with "
            "--episodes first. Clustering is only "
            "relevant when patients can contribute "
            "multiple episodes."
        )

    if args.cluster_by_patient:
        output_dir = (
            output_dir
            / "clustered_by_patient"
        )
    else:
        output_dir = (
            output_dir
            / "unclustered"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------------------------------------------
    # Predictor selection
    # ---------------------------------------------------------

    if variables_file is not None:

        predictors = (
            lrh.load_predictors_from_file(
                variables_file
            )
        )

        to_drop_comorbidities = [
            "asthma",
            "copd",
            "hypertension",
            "days_from_admission_to_first_culture",
            "crp",
        ]

        predictors = [
            p
            for p in predictors
            if p not in to_drop_comorbidities
        ]

    else:

        predictors = (
            lrh.get_configured_predictors(
                lr_config
            )
        )

        predictors = (
            lrh.clean_predictor_list(
                predictors,
                lr_config,
            )
        )

    if not predictors:
        raise ValueError(
            "No predictors available after "
            "excluding ID/outcome columns."
        )

    outcome = lr_config["outcome"]

    # ---------------------------------------------------------
    # Missingness before complete-case filtering
    # ---------------------------------------------------------

    missingness_cols = [
        outcome,
        *predictors,
    ]

    missingness_cols = [
        col
        for col in missingness_cols
        if col in lr_df.columns
    ]

    missingness_summary = pd.DataFrame(
        {
            "n_missing": (
                lr_df[missingness_cols]
                .isna()
                .sum()
            ),
            "n_non_missing": (
                lr_df[missingness_cols]
                .notna()
                .sum()
            ),
            "pct_missing": (
                lr_df[missingness_cols]
                .isna()
                .mean()
                .mul(100)
            ),
        }
    )

    missingness_summary.index.name = "variable"

    missingness_summary = (
        missingness_summary
        .sort_values(
            "pct_missing",
            ascending=False,
        )
        .reset_index()
    )

    # ---------------------------------------------------------
    # Create exact complete-case modelling dataframe
    # ---------------------------------------------------------

    model_df, X, y = (
        lrh.get_model_dataframe(
            lr_df,
            lr_config,
            predictors,
        )
    )

    n_before = len(lr_df)
    n_complete = len(model_df)
    n_lost = n_before - n_complete

    pct_complete = (
        100 * n_complete / n_before
        if n_before > 0
        else float("nan")
    )

    pct_lost = (
        100 * n_lost / n_before
        if n_before > 0
        else float("nan")
    )

    n_events_complete = int(
        y.sum()
    )

    n_non_events_complete = (
        n_complete
        - n_events_complete
    )

    event_pct_complete = (
        100
        * n_events_complete
        / n_complete
        if n_complete > 0
        else float("nan")
    )

    logging.info(
        "Rows before complete-case filtering: %s",
        n_before,
    )

    logging.info(
        "Rows after complete-case filtering: %s",
        n_complete,
    )

    logging.info(
        "Rows lost: %s (%.1f%%)",
        n_lost,
        pct_lost,
    )

    logging.info(
        "Events in complete-case cohort: %s",
        n_events_complete,
    )

    logging.info(
        "Non-events in complete-case cohort: %s",
        n_non_events_complete,
    )

    logging.info(
        "Event prevalence in complete-case cohort: %.1f%%",
        event_pct_complete,
    )

    # ---------------------------------------------------------
    # Save complete-case summary
    # ---------------------------------------------------------

    complete_case_summary = pd.DataFrame(
        {
            "metric": [
                "rows_before_complete_case_filtering",
                "rows_after_complete_case_filtering",
                "rows_lost",
                "pct_rows_complete",
                "pct_rows_lost",
                "events_complete_cases",
                "non_events_complete_cases",
                "event_pct_complete_cases",
            ],
            "value": [
                n_before,
                n_complete,
                n_lost,
                pct_complete,
                pct_lost,
                n_events_complete,
                n_non_events_complete,
                event_pct_complete,
            ],
        }
    )

    # ---------------------------------------------------------
    # Sequential complete-case loss
    # ---------------------------------------------------------

    sequential_rows = []

    tmp = lr_df.copy()
    previous_n = len(tmp)

    tmp = tmp[
        tmp[outcome].notna()
    ]

    sequential_rows.append(
        {
            "variable_added": outcome,
            "n_remaining": len(tmp),
            "n_dropped_at_step": (
                previous_n - len(tmp)
            ),
        }
    )

    previous_n = len(tmp)

    for predictor in predictors:

        if predictor not in tmp.columns:
            continue

        tmp = tmp[
            tmp[predictor].notna()
        ]

        current_n = len(tmp)

        sequential_rows.append(
            {
                "variable_added": predictor,
                "n_remaining": current_n,
                "n_dropped_at_step": (
                    previous_n - current_n
                ),
            }
        )

        previous_n = current_n

    sequential_missingness = pd.DataFrame(
        sequential_rows
    )

    # ---------------------------------------------------------
    # EPV
    # ---------------------------------------------------------

    epv_df = lrh.calculate_epv(
        y,
        n_predictors=X.shape[1],
    )

    # ---------------------------------------------------------
    # Correlation + VIF
    # ---------------------------------------------------------

    corr_matrix, high_corr_df = (
        lrh.get_correlation_screen(
            X,
            threshold=args.correlation_threshold,
        )
    )

    vif_df = lrh.calculate_vif(X)

    high_vif_df = (
        vif_df[
            (vif_df["variable"] != "const")
            & (
                vif_df["vif"]
                >= args.vif_threshold
            )
        ]
        .copy()
    )

    # ---------------------------------------------------------
    # Patient clustering
    # ---------------------------------------------------------

    groups = None

    if args.cluster_by_patient:

        groups = lr_df.loc[
            model_df.index,
            "subject",
        ]

        assert len(groups) == len(X)
        assert len(groups) == len(y)

    # ---------------------------------------------------------
    # Log model design
    # ---------------------------------------------------------

    print(
        "Final predictors list before model df:"
    )
    print(predictors)

    print(
        "Final X columns used in model:"
    )
    print(list(X.columns))

    # ---------------------------------------------------------
    # Fit full multivariable LR
    # ---------------------------------------------------------

    model, results_df = (
        lrh.fit_multivariable_lr(
            X,
            y,
            groups=groups,
        )
    )

    # ---------------------------------------------------------
    # Model summary
    # ---------------------------------------------------------

    summary_df = (
        lrh.get_model_summary(
            model
        )
    )

    logistic_coefficients = (
        model.params
    )

    # ---------------------------------------------------------
    # Save diagnostics
    # ---------------------------------------------------------

    missingness_summary.to_csv(
        output_dir
        / "missingness_summary.csv",
        index=False,
    )

    complete_case_summary.to_csv(
        output_dir
        / "complete_case_summary.csv",
        index=False,
    )

    sequential_missingness.to_csv(
        output_dir
        / "sequential_complete_case_loss.csv",
        index=False,
    )

    # ---------------------------------------------------------
    # Forest plot
    # ---------------------------------------------------------

    lrh.plot_adjusted_or_forest(
        results_df,
        save_path=(
            output_dir
            / "multi_lr_forest_plot.png"
        ),
    )

    # ---------------------------------------------------------
    # Standard outputs
    # ---------------------------------------------------------

    write_outputs(
        output_dir=output_dir,
        model_df=model_df,
        results_df=results_df,
        epv_df=epv_df,
        vif_df=vif_df,
        high_vif_df=high_vif_df,
        corr_matrix=corr_matrix,
        high_corr_df=high_corr_df,
        summary_df=summary_df,
        logistic_coefficients=logistic_coefficients,
    )

    # ---------------------------------------------------------
    # Final logging
    # ---------------------------------------------------------

    epv_row = epv_df.iloc[0]

    logging.info(
        "Wrote multivariable LR outputs to %s",
        output_dir,
    )

    logging.info(
        "Complete cases: %s; "
        "events: %s; "
        "non-events: %s; "
        "predictors: %s; "
        "EPV: %.2f",
        epv_row["n_complete_cases"],
        epv_row["events"],
        epv_row["non_events"],
        epv_row["n_predictors"],
        epv_row["epv"],
    )

    logging.info(
        "High VIF terms at >= %.2f: %s",
        args.vif_threshold,
        len(high_vif_df),
    )

    logging.info(
        "High-correlation pairs at >= %.2f: %s",
        args.correlation_threshold,
        len(high_corr_df),
    )


if __name__ == "__main__":
    main()