"""Tests for esbl_rf.models (synthetic data with known effects)."""
import numpy as np
import pandas as pd
import pytest

from esbl_rf.models import (check_both_outcomes, complete_case_loss, design_matrix, epv, first_episode_per_patient, fit_logit,
                            joint_p_value, merge_sparse_levels, multivariable, prepare_model_data, select_predictors,
                            subgroups, tidy_terms, univariable)


def simulated(n=1500, seed=1):
    """Episodes with a true OR of 3 for prior ESBL and 2 for urine vs blood; ~2 episodes per patient."""
    rng = np.random.default_rng(seed)
    prior = rng.integers(0, 2, n)
    site = rng.choice(["blood", "urine", "wound"], n)
    logit = -1 + np.log(3) * prior + np.log(2) * (site == "urine")
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return pd.DataFrame({
        "infection_id": [f"ep{i}" for i in range(n)],
        "subject": [f"p{i // 2}" for i in range(n)],
        "first_culture_dt": pd.Timestamp("2020-01-01") + pd.to_timedelta(rng.integers(0, 1000, n), unit="D"),
        "esbl_status": y,
        "age_at_admission": rng.integers(18, 90, n).astype(float),
        "imd_decile": rng.integers(1, 11, n).astype(float),
        "surgery_length": rng.uniform(0.5, 6, n),
        "cci_score": rng.integers(0, 6, n).astype(float),
        "past_abx": rng.integers(0, 2, n).astype(float),
        "inpatient_exposure_90d": rng.integers(0, 2, n).astype(float),
        "prior_positive_esbl": prior.astype(float),
        "gender": rng.choice(["female", "male"], n),
        "ethnicity_desc": rng.choice(["white", "asian", "black"], n),
        "organism_bug": rng.choice(["escherichia coli", "klebsiella pneumoniae"], n),
        "site": site,
        "tfc": rng.choice(["general_surgery", "vascular"], n),
        "prophylaxis_group": rng.choice(["cefuroxime +/- metronidazole", "co-amoxiclav-based"], n),
    })


def test_recovers_known_odds_ratios(cfg):
    df = simulated()
    terms, diag, vif = multivariable(df, ["prior_positive_esbl", "site"], cfg, "primary", clustered=True)
    or_ = terms.set_index(["variable", "level"])["odds_ratio"]
    assert or_[("prior_positive_esbl", np.nan)] == pytest.approx(3, rel=0.25)
    assert or_[("site", "urine")] == pytest.approx(2, rel=0.3)
    assert terms.loc[terms["variable"] == "site", "reference"].eq("blood").all()
    assert diag["n_parameters"] == 3 and diag["epv"] == pytest.approx(min(df["esbl_status"].sum(), (df["esbl_status"] == 0).sum()) / 3)
    assert diag["n_clusters"] == len(df) // 2 and not diag["problems"]
    assert len(vif) == 3 and vif["vif"].lt(5).all()


def test_clustering_changes_standard_errors_not_estimates(cfg):
    df = simulated()
    X, _ = design_matrix(df, ["prior_positive_esbl"], cfg)
    plain, _ = fit_logit(df["esbl_status"], X)
    clustered, _ = fit_logit(df["esbl_status"], X, df["subject"])
    assert np.allclose(plain.params, clustered.params)
    assert not np.allclose(plain.bse, clustered.bse)


def test_design_matrix_uses_configured_reference(cfg):
    df = pd.DataFrame({"site": ["urine", "urine", "blood", "wound"]})
    X, _ = design_matrix(df, ["site"], cfg)
    assert list(X.columns) == ["site__urine", "site__wound"]


def test_absent_reference_raises(cfg):
    with pytest.raises(ValueError, match="reference level 'blood'"):
        design_matrix(pd.DataFrame({"site": ["urine", "wound"]}), ["site"], cfg)


def test_constant_column_raises_unless_dropped_for_subgroups(cfg):
    df = pd.DataFrame({"cci_score": [1.0, 1.0], "age_at_admission": [50.0, 60.0]})
    with pytest.raises(ValueError, match="no variation"):
        design_matrix(df, ["cci_score", "age_at_admission"], cfg)
    X, dropped = design_matrix(df, ["cci_score", "age_at_admission"], cfg, drop_constant=True)
    assert list(X.columns) == ["age_at_admission"] and dropped == ["cci_score"]


def test_joint_p_of_single_term_equals_its_p(cfg):
    df = simulated()
    X, _ = design_matrix(df, ["prior_positive_esbl"], cfg)
    res, _ = fit_logit(df["esbl_status"], X, df["subject"])
    assert joint_p_value(res, "prior_positive_esbl") == pytest.approx(res.pvalues["prior_positive_esbl"])


def test_selection_keeps_screened_and_clinically_important(cfg):
    uni = univariable(simulated(), cfg)
    sel = select_predictors(uni, cfg).set_index("variable")
    assert sel.loc["site", "selected"] and "p < 0.2" in sel.loc["site", "reason"]
    assert sel.loc["age_at_admission", "selected"]          # clinically important whatever its p
    assert sel.loc["surgery_length", "reason"] in ("not selected", "p < 0.2")
    assert uni.groupby("variable")["joint_p"].nunique().eq(1).all()


def test_first_episode_by_date_not_string_id(cfg):
    df = pd.DataFrame({"subject": ["a", "a"], "infection_id": ["ep10", "ep9"],
                       "first_culture_dt": pd.to_datetime(["2020-01-01", "2020-02-01"])})
    assert first_episode_per_patient(df, cfg)["infection_id"].tolist() == ["ep10"]


def test_complete_case_loss_is_sequential(cfg):
    df = pd.DataFrame({"a": [1, None, 1, None], "b": [None, None, 1, 1]})
    out = complete_case_loss(df, ["a", "b"], cfg)
    assert out["n_dropped"].tolist() == [2, 1] and out["n_remaining"].tolist() == [2, 1]


def test_sparse_level_merged_and_logged(cfg):
    df = pd.DataFrame({"esbl_status": [1] * 20 + [0] * 20 + [1] * 3 + [0] * 30,
                       "site": ["blood"] * 40 + ["sputum"] * 33})
    for var in cfg["logistic_regression"]["predictors"]["categorical"]:
        if var != "site":
            df[var] = pd.NA            # only site is tested
    out, merged = merge_sparse_levels(df, cfg)
    assert set(out["site"]) == {"blood", "other"}
    assert merged.to_dict("records") == [{"variable": "site", "level": "sputum", "merged_into": "other",
                                          "events": 3, "non_events": 30}]


def test_sparse_reference_raises(cfg):
    df = pd.DataFrame({"esbl_status": [1] * 3 + [0] * 30, "site": ["blood"] * 33})
    for var in cfg["logistic_regression"]["predictors"]["categorical"]:
        if var != "site":
            df[var] = pd.NA            # only site is tested
    with pytest.raises(ValueError, match="reference level 'blood'"):
        merge_sparse_levels(df, cfg)


def test_prepare_rejects_bad_binary(cfg):
    df = simulated(200).assign(past_abx=2.0)
    with pytest.raises(ValueError, match="binary predictor 'past_abx'"):
        prepare_model_data(df, cfg)


def test_epv_counts_every_parameter():
    assert epv(pd.Series([1] * 20 + [0] * 80), 4) == 5


def test_subgroups_skip_low_epv(cfg):
    df = simulated()
    small = df[df["tfc"] == "vascular"].head(30)
    out = subgroups(pd.concat([df[df["tfc"] == "general_surgery"], small]), ["prior_positive_esbl", "site", "tfc"], cfg)
    tfc = out[out["subgroup"] == "tfc"].groupby("stratum")["status"].first()
    assert tfc["general_surgery"] == "ok"
    assert tfc["vascular"].startswith("not estimable: EPV")
    assert "tfc" not in set(out.loc[out["subgroup"] == "tfc", "variable"].dropna())


def test_tidy_terms_reports_reference_only_for_levels(cfg):
    df = simulated()
    X, _ = design_matrix(df, ["age_at_admission", "site"], cfg)
    res, _ = fit_logit(df["esbl_status"], X)
    t = tidy_terms(res, "m", df["esbl_status"], cfg)
    assert t.loc[t["variable"] == "age_at_admission", "reference"].isna().all()
    assert (t["ci_lower"] < t["odds_ratio"]).all() and (t["odds_ratio"] < t["ci_upper"]).all()


def separated(seed=2):
    """Age perfectly predicts the outcome (complete separation on a continuous predictor,
    which check_both_outcomes does not look at)."""
    df = simulated(seed=seed)
    df["esbl_status"] = (df["age_at_admission"] > 50).astype(int)
    return df


def test_separated_multivariable_stops_the_run(cfg):
    with pytest.raises(ValueError, match="not valid"):
        multivariable(separated(), ["age_at_admission", "site"], cfg, "primary", clustered=True)


def test_separated_univariable_term_has_no_estimate(cfg):
    uni = univariable(separated(), cfg)
    bad = uni[uni["variable"] == "age_at_admission"].iloc[0]
    assert bad["problems"] and pd.isna(bad["odds_ratio"]) and pd.isna(bad["joint_p"])
    assert uni.loc[uni["variable"] == "imd_decile", "odds_ratio"].notna().all()


def test_separated_subgroup_is_not_estimable(cfg):
    df = simulated()
    gs = df["tfc"] == "general_surgery"
    df.loc[gs, "esbl_status"] = df.loc[gs, "prior_positive_esbl"].astype(int)
    out = subgroups(df, ["prior_positive_esbl", "site", "tfc"], cfg)
    tfc = out[out["subgroup"] == "tfc"].set_index("stratum")
    assert tfc.loc["general_surgery", "status"].startswith("not estimable")
    assert pd.isna(tfc.loc["general_surgery", "odds_ratio"])
    assert tfc.loc["vascular"]["status"].eq("ok").all()


def test_level_with_no_events_stops_the_model(cfg):
    """16 genital episodes, none ESBL, gave OR 0.00 (0.00-0.00) in the SDE run of 2026-11-26."""
    df = simulated()
    df.loc[df.index[:16], ["site", "esbl_status"]] = ["other", 0]
    with pytest.raises(ValueError, match=r"multivariable\[primary\].*site = other \(0 events, 16 non-events\)"):
        multivariable(df, ["prior_positive_esbl", "site"], cfg, "primary", clustered=True)
    with pytest.raises(ValueError, match=r"univariable\[site\]"):
        univariable(df, cfg)


def test_level_with_no_non_events_stops_the_model(cfg):
    df = simulated()
    df.loc[df.index[:12], ["site", "esbl_status"]] = ["other", 1]
    with pytest.raises(ValueError, match=r"site = other \(12 events, 0 non-events\)"):
        check_both_outcomes(df, ["site"], cfg, "test")


def test_separation_checked_after_complete_case(cfg):
    """A level can lose all its events only once rows with missing values are dropped."""
    df = simulated()
    df.loc[df.index[:20], "site"] = "other"
    df.loc[df.index[:20], "esbl_status"] = [1] * 5 + [0] * 15
    df.loc[df.index[:5], "imd_decile"] = np.nan
    check_both_outcomes(df, ["site"], cfg, "test")
    with pytest.raises(ValueError, match="site = other"):
        multivariable(df, ["site", "imd_decile"], cfg, "primary", clustered=True)


def test_binary_predictor_separation_is_caught(cfg):
    df = simulated()
    df["past_abx"] = 0.0
    df.loc[df["esbl_status"] == 1, "past_abx"] = 1.0
    with pytest.raises(ValueError, match="past_abx"):
        check_both_outcomes(df, ["past_abx"], cfg, "test")


def test_ordinary_data_passes(cfg):
    check_both_outcomes(simulated(), ["site", "prior_positive_esbl", "age_at_admission"], cfg, "test")
