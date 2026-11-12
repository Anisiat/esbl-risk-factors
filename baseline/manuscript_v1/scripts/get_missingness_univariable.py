"""Run univariable logistic regression for the ESBL analysis.

This script is intentionally thin: it parses command-line options, loads the
analysis dataframe/config, then delegates the reusable regression work to
``src/utils/lr_helper_functions.py``. The main output, ``lr_data.csv``, is the
clean LR-ready dataframe used by the multivariable model script.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from utils import lr_helper_functions as lrh  # noqa: E402


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.yaml"
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "interim" / "analysis_df.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "univariable_lr"


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the univariable LR workflow."""
    parser = argparse.ArgumentParser(
        description="Run univariable logistic regression from the analysis dataframe."
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
        help="Path to analysis_df CSV. Defaults to data/interim/analysis_df.csv.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for output CSV files. Defaults to outputs/univariable_lr/.",
    )
    parser.add_argument(
        "--include-non-surgical",
        action="store_true",
        help="Do not restrict to surgery_before_infection == 1.",
    )
    parser.add_argument(
        '--cluster-by-patient', action = 'store_true',
        help = 'Use patient-clustered standard errors'
    )
    parser.add_argument(
        "--p-threshold",
        type=float,
        default=0.2,
        help="P-value threshold for candidate variable selection.",
    )
    parser.add_argument(
        "--skip-notebook-recodes",
        action="store_true",
        help="Skip prophylaxis, ethnicity, and gender recodes used in the notebook.",
    )

    parser.add_argument(
        "--episodes",
        choices = ['all', 'first'],
        help="Run analysis on all episodes or just first episodes.",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    """Resolve a CLI path relative to the project root unless already absolute."""
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(config_path: Path) -> dict:
    """Load the project YAML config using the shared repo config loader."""
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
    uni_lr_df: pd.DataFrame,
    continuous_results: pd.DataFrame,
    binary_results: pd.DataFrame,
    categorical_results: pd.DataFrame,
    combined_results: pd.DataFrame,
    selected_variables: list[str],
) -> None:
    """Write LR-ready data, per-type results, combined results, and screened variables."""
    output_dir.mkdir(parents=True, exist_ok=True)

    uni_lr_df.to_csv(PROJECT_ROOT / 'data' / 'processed'/ "lr_data.csv", index=False)
    # continuous_results.to_csv(output_dir / "univariable_lr_continuous_results.csv", index=False)
    # binary_results.to_csv(output_dir / "univariable_lr_binary_results.csv", index=False)
    # categorical_results.to_csv(output_dir / "univariable_lr_categorical_results.csv", index=False)
    combined_results.to_csv(output_dir / "univariable_lr_results.csv", index=False)

    selected_df = pd.DataFrame({"predictor": selected_variables})
    selected_df.to_csv(output_dir / "variables_for_multivariable_lr.csv", index=False)

def main() -> None:
    """Run the univariable logistic regression workflow and save outputs."""

    args = parse_args()

    config_path = resolve_path(args.config)
    input_path = resolve_path(args.input)
    output_dir = resolve_path(args.output_dir)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logging.info("Loading config from %s", config_path)
    cfg = load_config(config_path)
    lr_config = cfg["logistic_regression"]

    logging.info("Loading analysis data from %s", input_path)
    analysis_df = lrh.load_analysis_data(input_path)

    # ---------------------------------------------------------
    # Restrict to first episode if requested
    # ---------------------------------------------------------

    if args.episodes == "first":
        analysis_df = lrh.restrict_to_first_episode(analysis_df)
        output_dir = output_dir / "first_episodes"
    else:
        output_dir = output_dir / "all_episodes"

    if args.cluster_by_patient and args.episodes == 'first':
        raise ValueError('Do not use --cluster-by-patient with --episodes first')

    cluster_col = 'subject' if args.cluster_by_patient else None

    output_dir = output_dir/ ('cluster_by_patient' if cluster_col else 'unclustered')

    output_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------
    # Optional recoding / preprocessing
    # ---------------------------------------------------------

    if not args.skip_notebook_recodes:
        analysis_df = lrh.apply_ethnicity_gender_maps(analysis_df)

    # ---------------------------------------------------------
    # Validate and prepare dataframe
    # ---------------------------------------------------------

    lrh.validate_lr_columns(
        analysis_df,
        lr_config,
    )

    uni_lr_df = lrh.prepare_lr_df(
        analysis_df,
        lr_config,
    )

    if cluster_col and cluster_col not in uni_lr_df.columns:
        raise KeyError(f"'{cluster_col}' missing from uni_lr_df")

    outcome = lr_config["outcome"]

    predictor_groups = lrh.get_predictor_groups(
        lr_config
    )

    reference_levels = lr_config.get(
        "reference_levels",
        {},
    )

    # ---------------------------------------------------------
    # Candidate variables
    # ---------------------------------------------------------

    all_predictors = (
        predictor_groups["continuous"]
        + predictor_groups["binary"]
        + predictor_groups["categorical"]
    )

    all_model_cols = [outcome] + all_predictors

    # ---------------------------------------------------------
    # Missingness summary across all candidate variables
    # ---------------------------------------------------------

    missingness_summary = (
        uni_lr_df[all_model_cols]
        .isna()
        .agg(["sum", "mean"])
        .T
        .rename(
            columns={
                "sum": "n_missing",
                "mean": "prop_missing",
            }
        )
    )

    missingness_summary["pct_missing"] = (
        missingness_summary["prop_missing"] * 100
    )

    missingness_summary["n_complete"] = (
        len(uni_lr_df)
        - missingness_summary["n_missing"]
    )

    missingness_summary = (
        missingness_summary
        .drop(columns="prop_missing")
        .sort_values(
            "pct_missing",
            ascending=False,
        )
    )

    missingness_summary.to_csv(
        output_dir / "missingness_summary.csv"
    )

    logging.info(
        "Saved missingness summary to %s",
        output_dir / "missingness_summary.csv",
    )

    # ---------------------------------------------------------
    # Run univariable analyses
    # ---------------------------------------------------------

    continuous_results = lrh.run_univariable_numeric_lr(
        uni_lr_df=uni_lr_df,
        variables=predictor_groups["continuous"],
        outcome=outcome,
        model_type="continuous",
        cluster_col = cluster_col
    )

    binary_results = lrh.run_univariable_numeric_lr(
        uni_lr_df=uni_lr_df,
        variables=predictor_groups["binary"],
        outcome=outcome,
        model_type="binary",
        cluster_col = cluster_col
    )

    categorical_results = lrh.run_univariable_categorical_lr(
        uni_lr_df=uni_lr_df,
        categorical_vars=predictor_groups["categorical"],
        outcome=outcome,
        reference_levels=reference_levels,
        cluster_col = cluster_col
    )

    # ---------------------------------------------------------
    # Combine univariable results
    # ---------------------------------------------------------

    combined_results = (
        pd.concat(
            [
                continuous_results,
                binary_results,
                categorical_results,
            ],
            ignore_index=True,
        )
        .sort_values(
            ["model_type", "predictor", "p_value"],
            na_position="last",
        )
    )

    # ---------------------------------------------------------
    # Select variables for multivariable model
    # ---------------------------------------------------------

    selected_variables = (
        lrh.select_variables_for_multivariable_lr(
            combined_results,
            p_threshold=args.p_threshold,
        )
    )

    # ---------------------------------------------------------
    # Complete-case analysis for selected multivariable variables
    # ---------------------------------------------------------

    selected_model_cols = (
        [outcome]
        + selected_variables
    )

    complete_case_df = (
        uni_lr_df[selected_model_cols]
        .dropna()
        .copy()
    )

    n_total = len(uni_lr_df)
    n_complete = len(complete_case_df)

    n_events_total = (
        uni_lr_df[outcome]
        .dropna()
        .sum()
    )

    n_events_complete = (
        complete_case_df[outcome]
        .sum()
    )

    complete_case_pct = (
        100 * n_complete / n_total
        if n_total > 0
        else float("nan")
    )

    event_pct_complete = (
        100 * n_events_complete / n_complete
        if n_complete > 0
        else float("nan")
    )

    logging.info(
        "Total analysis rows: %s",
        n_total,
    )

    logging.info(
        "Complete-case rows for selected multivariable model: "
        "%s (%.1f%%)",
        n_complete,
        complete_case_pct,
    )

    logging.info(
        "Total outcome events: %s",
        int(n_events_total),
    )

    logging.info(
        "Outcome events in complete-case multivariable cohort: "
        "%s (%.1f%%)",
        int(n_events_complete),
        event_pct_complete,
    )

    # ---------------------------------------------------------
    # Save complete-case summary
    # ---------------------------------------------------------

    complete_case_summary = pd.DataFrame(
        {
            "metric": [
                "total_rows",
                "complete_case_rows",
                "complete_case_pct",
                "total_events",
                "complete_case_events",
                "complete_case_event_pct",
            ],
            "value": [
                n_total,
                n_complete,
                complete_case_pct,
                int(n_events_total),
                int(n_events_complete),
                event_pct_complete,
            ],
        }
    )

    complete_case_summary.to_csv(
        output_dir / "complete_case_summary.csv",
        index=False,
    )

    # ---------------------------------------------------------
    # Sequential complete-case loss
    #
    # Shows how N falls as each selected variable is added.
    # Useful for identifying which variables drive exclusions.
    # ---------------------------------------------------------

    sequential_rows = []

    remaining_df = uni_lr_df.copy()

    # outcome first
    remaining_df = remaining_df[
        remaining_df[outcome].notna()
    ]

    sequential_rows.append(
        {
            "variable_added": outcome,
            "n_remaining": len(remaining_df),
            "n_dropped_at_step": (
                n_total - len(remaining_df)
            ),
        }
    )

    previous_n = len(remaining_df)

    for variable in selected_variables:

        remaining_df = remaining_df[
            remaining_df[variable].notna()
        ]

        current_n = len(remaining_df)

        sequential_rows.append(
            {
                "variable_added": variable,
                "n_remaining": current_n,
                "n_dropped_at_step": (
                    previous_n - current_n
                ),
            }
        )

        previous_n = current_n

    sequential_complete_case = pd.DataFrame(
        sequential_rows
    )

    sequential_complete_case.to_csv(
        output_dir / "sequential_complete_case_loss.csv",
        index=False,
    )

    # ---------------------------------------------------------
    # Save standard univariable outputs
    # ---------------------------------------------------------

    write_outputs(
        output_dir=output_dir,
        uni_lr_df=uni_lr_df,
        continuous_results=continuous_results,
        binary_results=binary_results,
        categorical_results=categorical_results,
        combined_results=combined_results,
        selected_variables=selected_variables,
    )

    # ---------------------------------------------------------
    # Logging summary
    # ---------------------------------------------------------

    failed = (
        combined_results["error"]
        .notna()
        .sum()
    )

    logging.info(
        "Wrote univariable LR outputs to %s",
        output_dir,
    )

    logging.info(
        "Rows fitted: %s; failed model rows: %s",
        len(combined_results),
        failed,
    )

    logging.info(
        "Selected variables at p < %.3f: %s",
        args.p_threshold,
        selected_variables,
    )

    logging.info(
        "Final multivariable complete-case N = %s; "
        "events = %s",
        n_complete,
        int(n_events_complete),
    )


if __name__ == "__main__":
    main()
