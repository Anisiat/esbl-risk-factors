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