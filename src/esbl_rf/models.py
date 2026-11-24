"""Logistic regression: univariable screen, multivariable model, sensitivity and subgroups.

Analysis plan section 8. Ported from the manuscript's ``lr_helper_functions.py`` and
``get_missingness_{uni,multi}variable.py``. Odds ratios only: the train/test
split, AUROC and bootstrap are dropped (decided 2026-11-23). CHANGES (fragile
baseline behaviour made explicit):

- reference levels are set before dummy coding and must exist in the data (the
  baseline dropped them with ``errors="ignore"``, so a misspelt reference
  silently changed the comparison);
- a predictor column with no variation raises instead of being dropped silently;
  in subgroups, terms with no variation in a stratum are dropped and reported;
- the first episode per patient is the earliest by first culture time (the
  baseline sorted the string ``infection_id``);
- variables are screened with one joint Wald test per variable (the baseline
  used the smallest p-value of any level);
- sparse levels are merged by a configured rule and logged.
"""
from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.tools.sm_exceptions import ConvergenceWarning, PerfectSeparationError, PerfectSeparationWarning

from esbl_rf.tables import level_order

log = logging.getLogger(__name__)

SEP = "__"   # design column = <variable>__<level> for categorical levels
TERM_COLUMNS = ["model", "variable", "level", "reference", "odds_ratio", "ci_lower", "ci_upper", "p_value",
                "n", "events"]


def predictor_types(cfg: dict) -> dict[str, str]:
    """Map each configured predictor to 'continuous', 'binary' or 'categorical' (config order)."""
    preds = cfg["logistic_regression"]["predictors"]
    return {v: kind for kind in ("continuous", "binary", "categorical") for v in preds[kind]}


# --- data preparation ----------------------------------------------------

def prepare_model_data(episodes: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model-ready episodes: typed predictors and sparse levels merged.

    Grain: one row per infection episode (unchanged). Keeps the outcome, the
    cluster and order columns, ``infection_id`` and the predictors. Missing values
    stay missing (complete-case analysis happens per model).

    Returns
    -------
    (data, merged)
        ``merged`` lists every level merged by ``logistic_regression.sparse``.

    Raises
    ------
    ValueError
        On absent columns, a binary predictor with values other than 0/1, text in
        a continuous predictor, or a sparse reference level.
    """
    lr, outcome = cfg["logistic_regression"], cfg["columns"]["outcome"]
    types = predictor_types(cfg)
    keep = ["infection_id", lr["cluster_col"], lr["order_col"], outcome, *types]
    absent = [c for c in keep if c not in episodes.columns]
    if absent:
        raise ValueError(f"prepare_model_data: episodes is missing column(s) {absent}")
    df = episodes[keep].copy()
    bad = df[outcome].isna() | ~df[outcome].isin([0, 1])
    if bad.any():
        raise ValueError(f"prepare_model_data: {int(bad.sum())} episode(s) have an outcome that is not 0/1")
    df[outcome] = df[outcome].astype(int)

    for var, kind in types.items():
        if kind == "categorical":
            df[var] = df[var].astype("string")
            continue
        numeric = pd.to_numeric(df[var], errors="coerce")
        n_text = int((numeric.isna() & df[var].notna()).sum())
        if n_text:
            raise ValueError(f"prepare_model_data: predictor '{var}' has {n_text} non-numeric value(s)")
        if kind == "binary" and not numeric.dropna().isin([0, 1]).all():
            raise ValueError(f"prepare_model_data: binary predictor '{var}' has values other than 0/1")
        df[var] = numeric.astype(float)

    df, merged = merge_sparse_levels(df, cfg)
    return df, merged


def merge_sparse_levels(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merge non-reference levels with too few events or non-events (``logistic_regression.sparse``).

    A level is sparse if it has fewer than ``min_events`` ESBL-positive or fewer
    than ``min_events`` non-ESBL episodes, counted over all episodes. Sparse levels
    of a variable with a ``merge_into`` target are merged into it; if the target
    is itself still sparse afterwards, or the variable has no target, a warning is
    logged and the row in ``merged`` says so. A sparse reference level raises.
    """
    lr, outcome = cfg["logistic_regression"], cfg["columns"]["outcome"]
    min_events, targets = lr["sparse"]["min_events"], lr["sparse"]["merge_into"]
    out, rows = df.copy(), []
    for var in lr["predictors"]["categorical"]:
        counts = pd.crosstab(out[var], out[outcome]).reindex(columns=[0, 1], fill_value=0)
        sparse = counts[(counts[0] < min_events) | (counts[1] < min_events)].index.tolist()
        ref = lr["reference_levels"][var]
        if ref in sparse:
            raise ValueError(f"merge_sparse_levels: reference level '{ref}' of '{var}' has fewer than {min_events} "
                             "events or non-events; choose another reference in config.yaml")
        target = targets.get(var)
        sparse = [lvl for lvl in sparse if lvl != target]
        if not sparse:
            continue
        for lvl in sparse:
            rows.append({"variable": var, "level": lvl, "merged_into": target, "events": int(counts.loc[lvl, 1]),
                         "non_events": int(counts.loc[lvl, 0])})
        if target is None:
            log.warning("merge_sparse_levels: '%s' has sparse level(s) %s and no merge_into target; kept", var, sparse)
            continue
        out.loc[out[var].isin(sparse), var] = target
        after = out.loc[out[var] == target, outcome].value_counts().reindex([0, 1], fill_value=0)
        log.info("merge_sparse_levels: '%s' level(s) %s merged into '%s'", var, sparse, target)
        if after.min() < min_events:
            log.warning("merge_sparse_levels: merged level '%s' of '%s' is still sparse (%d events, %d non-events)",
                        target, var, after[1], after[0])
    merged = pd.DataFrame(rows, columns=["variable", "level", "merged_into", "events", "non_events"])
    return out, merged


def first_episode_per_patient(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Keep each patient's earliest episode (by ``order_col``, ties by ``infection_id``).

    Grain: one row per patient. Chosen from all episodes, before complete-case
    filtering, so the choice does not depend on which variables are missing.
    """
    lr = cfg["logistic_regression"]
    order = pd.to_datetime(df[lr["order_col"]], errors="coerce")
    if order.isna().any():
        raise ValueError(f"first_episode_per_patient: {int(order.isna().sum())} episode(s) have no '{lr['order_col']}'")
    first = (df.assign(_order=order).sort_values([lr["cluster_col"], "_order", "infection_id"], kind="stable")
             .drop_duplicates(lr["cluster_col"]).drop(columns="_order"))
    return first


def complete_case_loss(df: pd.DataFrame, predictors: list[str], cfg: dict) -> pd.DataFrame:
    """Episodes lost to missing data, one predictor at a time in the given order.

    Grain: one row per predictor. ``n_remaining`` is the number of episodes with no
    missing value in that predictor or any earlier one.
    """
    keep = pd.Series(True, index=df.index)
    rows = []
    for var in predictors:
        missing = df[var].isna()
        rows.append({"variable": var, "n_missing": int(missing.sum()), "n_dropped": int((keep & missing).sum())})
        keep &= ~missing
        rows[-1]["n_remaining"] = int(keep.sum())
    return pd.DataFrame(rows, columns=["variable", "n_missing", "n_dropped", "n_remaining"])


# --- design matrix and fitting -------------------------------------------

def design_matrix(df: pd.DataFrame, predictors: list[str], cfg: dict,
                  drop_constant: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Numeric design matrix (no constant) for complete cases ``df``.

    Categorical predictors are dummy-coded against ``reference_levels``; columns
    are named ``<variable>__<level>``.

    Parameters
    ----------
    drop_constant : bool
        For subgroups: drop columns with no variation in this subset (e.g. a flag
        nobody in the stratum has) and return their names, instead of raising.

    Raises
    ------
    ValueError
        If a reference level is absent, or a column has no variation and
        ``drop_constant`` is False.
    """
    types, refs = predictor_types(cfg), cfg["logistic_regression"]["reference_levels"]
    parts = []
    for var in predictors:
        if types[var] != "categorical":
            parts.append(df[[var]].astype(float))
            continue
        ref = refs[var]
        if ref not in set(df[var]):
            raise ValueError(f"design_matrix: reference level '{ref}' of '{var}' is absent from the data")
        levels = level_order(df[var], var, cfg)
        cat = pd.Categorical(df[var], categories=levels)
        dummies = pd.get_dummies(cat, prefix=var, prefix_sep=SEP, dtype=float).iloc[:, 1:]
        dummies.index = df.index
        parts.append(dummies)
    X = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=df.index)
    flat = [c for c in X.columns if X[c].nunique() < 2]
    if flat and drop_constant:
        return X.drop(columns=flat), flat
    if flat:
        raise ValueError(f"design_matrix: column(s) with no variation: {flat}")
    return X, []


def split_term(term: str) -> tuple[str, str | None]:
    """``'site__urine'`` -> ``('site', 'urine')``; ``'age_at_admission'`` -> ``('age_at_admission', None)``."""
    var, _, level = term.partition(SEP)
    return var, (level or None)


def fit_logit(y: pd.Series, X: pd.DataFrame, groups: pd.Series | None = None):
    """Fit a logistic regression with optional cluster-robust standard errors.

    Returns
    -------
    (result, problems)
        ``problems`` lists convergence or separation warnings (empty if none);
        callers record them in the diagnostics.
    """
    exog = sm.add_constant(X, has_constant="add")
    kwargs = {"disp": 0, "maxiter": 200}
    if groups is not None:
        kwargs.update(cov_type="cluster", cov_kwds={"groups": pd.factorize(groups)[0]})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = sm.Logit(y.astype(float), exog).fit(**kwargs)
    problems = [str(w.message) for w in caught if issubclass(w.category, (ConvergenceWarning, PerfectSeparationWarning))]
    if not result.mle_retvals.get("converged", True):
        problems.append("did not converge")
    return result, sorted(set(problems))


def tidy_terms(result, model: str, y: pd.Series, cfg: dict) -> pd.DataFrame:
    """One row per model term (constant excluded): OR, 95% CI and p-value."""
    refs = cfg["logistic_regression"]["reference_levels"]
    conf = result.conf_int()
    rows = []
    for term in result.params.index:
        if term == "const":
            continue
        var, level = split_term(term)
        rows.append({"model": model, "variable": var, "level": level, "reference": refs.get(var) if level else None,
                     "odds_ratio": float(np.exp(result.params[term])), "ci_lower": float(np.exp(conf.loc[term, 0])),
                     "ci_upper": float(np.exp(conf.loc[term, 1])), "p_value": float(result.pvalues[term]),
                     "n": len(y), "events": int(y.sum())})
    return pd.DataFrame(rows, columns=TERM_COLUMNS)


def joint_p_value(result, variable: str) -> float:
    """Wald test that all terms of ``variable`` are zero (cluster-robust if the fit was)."""
    names = list(result.params.index)
    idx = [i for i, t in enumerate(names) if split_term(t)[0] == variable]
    r = np.zeros((len(idx), len(names)))
    r[np.arange(len(idx)), idx] = 1
    return float(result.wald_test(r, scalar=True).pvalue)


def epv(y: pd.Series, n_parameters: int) -> float:
    """Events per variable: the smaller outcome class divided by the number of parameters (each dummy counts)."""
    limiting = min(int(y.sum()), int((y == 0).sum()))
    return limiting / n_parameters if n_parameters else float("nan")


def vif_table(X: pd.DataFrame) -> pd.DataFrame:
    """Variance inflation factor for each design column (computed with a constant, which is not reported)."""
    exog = sm.add_constant(X, has_constant="add")
    rows = [{"term": col, "vif": float(variance_inflation_factor(exog.values, i))}
            for i, col in enumerate(exog.columns) if col != "const"]
    return pd.DataFrame(rows, columns=["term", "vif"])


# --- analyses -------------------------------------------------------------

def univariable(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One cluster-robust logistic regression per predictor, on episodes with that predictor recorded.

    Grain: one row per term, plus ``joint_p`` (the variable's joint Wald p-value,
    repeated on each of its rows) and ``problems``.
    """
    lr, outcome = cfg["logistic_regression"], cfg["columns"]["outcome"]
    out = []
    for var in predictor_types(cfg):
        data = df.dropna(subset=[var])
        X, _ = design_matrix(data, [var], cfg)
        result, problems = fit_logit(data[outcome], X, data[lr["cluster_col"]])
        terms = tidy_terms(result, "univariable", data[outcome], cfg)
        terms["joint_p"] = joint_p_value(result, var)
        terms["problems"] = "; ".join(problems) or None
        if problems:
            log.warning("univariable[%s]: %s", var, problems)
        out.append(terms)
    return pd.concat(out, ignore_index=True)


def select_predictors(uni: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Candidate predictors for the multivariable model, with the reason for each.

    Grain: one row per configured predictor (config order). ``selected`` is True if
    the joint univariable p-value is below ``screen_p`` or the predictor is in
    ``clinically_important``.
    """
    lr = cfg["logistic_regression"]
    p = uni.groupby("variable", sort=False)["joint_p"].first()
    rows = []
    for var in predictor_types(cfg):
        screened = bool(p[var] < lr["screen_p"])
        important = var in lr["clinically_important"]
        reason = " and ".join(r for r, ok in ((f"p < {lr['screen_p']}", screened),
                                              ("clinically important", important)) if ok) or "not selected"
        rows.append({"variable": var, "joint_p": float(p[var]), "selected": screened or important, "reason": reason})
    out = pd.DataFrame(rows)
    log.info("select_predictors: %s", out.set_index("variable")["reason"].to_dict())
    return out


def multivariable(df: pd.DataFrame, predictors: list[str], cfg: dict, model: str,
                  clustered: bool) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Complete-case multivariable logistic regression.

    Returns
    -------
    (terms, diagnostics, vif)
        ``terms``: one row per term. ``diagnostics``: one dict (n, events, EPV,
        maximum VIF, problems). ``vif``: one row per design column.
    """
    lr, outcome = cfg["logistic_regression"], cfg["columns"]["outcome"]
    data = df.dropna(subset=predictors)
    X, _ = design_matrix(data, predictors, cfg)
    y = data[outcome]
    result, problems = fit_logit(y, X, data[lr["cluster_col"]] if clustered else None)
    vif = vif_table(X).assign(model=model, flagged=lambda v: v["vif"] > lr["vif_flag"])
    diag = {"model": model, "n_episodes": len(df), "n_used": len(y), "events": int(y.sum()),
            "non_events": int((y == 0).sum()), "n_parameters": X.shape[1], "epv": epv(y, X.shape[1]),
            "max_vif": float(vif["vif"].max()) if len(vif) else float("nan"),
            "n_vif_flagged": int(vif["flagged"].sum()), "clustered": clustered,
            "n_clusters": int(data[lr["cluster_col"]].nunique()), "problems": "; ".join(problems) or None}
    if problems:
        log.warning("multivariable[%s]: %s", model, problems)
    log.info("multivariable[%s]: n=%d, events=%d, parameters=%d, EPV=%.1f, max VIF=%.2f", model, diag["n_used"],
             diag["events"], diag["n_parameters"], diag["epv"], diag["max_vif"])
    return tidy_terms(result, model, y, cfg), diag, vif


def subgroups(df: pd.DataFrame, predictors: list[str], cfg: dict) -> pd.DataFrame:
    """The primary model within each level of each ``subgroups`` variable (exploratory).

    Grain: one row per (stratum, term) for estimated strata, and one row per
    stratum that was not estimated. The stratifying variable is left out of its
    own strata. Strata with EPV below ``min_subgroup_epv`` are not fitted;
    EPV below ``stable_subgroup_epv`` is flagged as unstable. Terms with no
    variation in a stratum are dropped and listed in ``dropped_terms``.
    """
    lr, outcome = cfg["logistic_regression"], cfg["columns"]["outcome"]
    out = []
    for by in lr["subgroups"]:
        preds = [p for p in predictors if p != by]
        for stratum in level_order(df[by], by, cfg):
            data = df[df[by] == stratum].dropna(subset=preds)
            y = data[outcome]
            base = {"subgroup": by, "stratum": stratum, "n": len(y), "events": int(y.sum())}
            try:
                X, dropped = design_matrix(data, preds, cfg, drop_constant=True)
            except ValueError as err:
                out.append(pd.DataFrame([{**base, "status": f"not estimable: {err}"}]))
                continue
            base.update(n_parameters=X.shape[1], epv=epv(y, X.shape[1]), dropped_terms="; ".join(dropped) or None)
            if not base["epv"] >= lr["min_subgroup_epv"]:
                out.append(pd.DataFrame([{**base, "status": f"not estimable: EPV < {lr['min_subgroup_epv']}"}]))
                continue
            try:
                result, problems = fit_logit(y, X, data[lr["cluster_col"]])
            except (np.linalg.LinAlgError, PerfectSeparationError) as err:
                out.append(pd.DataFrame([{**base, "status": f"not estimable: {err}"}]))
                continue
            status = "unstable: EPV < %s" % lr["stable_subgroup_epv"] if base["epv"] < lr["stable_subgroup_epv"] else "ok"
            terms = tidy_terms(result, "subgroup", y, cfg).drop(columns=["model", "n", "events"])
            out.append(terms.assign(**base, status=status, problems="; ".join(problems) or None))
    cols = ["subgroup", "stratum", "status", "n", "events", "n_parameters", "epv", "dropped_terms", "variable",
            "level", "reference", "odds_ratio", "ci_lower", "ci_upper", "p_value", "problems"]
    return pd.concat(out, ignore_index=True).reindex(columns=cols)
