"""ESBL labelling of samples (analysis_plan section 6)."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "label_esbl_status"


def _tidy(text: pd.Series) -> pd.Series:
    """Lower-case, trim, and put single spaces around the first colon: ``'Drug:X'`` -> ``'drug : x'``."""
    return text.astype("string").str.lower().str.strip().str.replace(r"\s*:\s*", " : ", n=1, regex=True)


def build_ast_rules(resistant: pd.DataFrame, susceptible: pd.DataFrame) -> pd.DataFrame:
    """Combine the 3GCR and 3GCS lookups into one table of rules.

    Grain of the inputs: one row per distinct ``sensitivity`` string (``'drug : result'``).
    The lookups are the only place a string is split, to read off its drug.
    CHANGE vs baseline (``get_ast_rules``): the drug is kept, so a result is
    matched for the right antibiotic; the baseline kept only the result part.

    Returns
    -------
    pd.DataFrame
        Grain: one row per string. Columns ``sensitivity`` (tidied string), ``drug``
        and ``rule`` (``'resistant'`` or ``'susceptible'``).

    Raises
    ------
    ValueError
        If a string has no ``':'``, or a string is listed more than once (in both lookups, or twice in one).
    """
    frames = []
    for df, rule in ((resistant, "resistant"), (susceptible, "susceptible")):
        strings = _tidy(df["sensitivity"])
        bad = strings[~strings.str.contains(" : ", regex=False)]
        if not bad.empty:
            raise ValueError(f"build_ast_rules: {len(bad)} {rule} lookup string(s) have no ':' (e.g. {bad.iloc[0]!r})")
        frames.append(pd.DataFrame({"sensitivity": strings, "drug": strings.str.split(" : ", n=1).str[0], "rule": rule}))
    rules = pd.concat(frames, ignore_index=True)
    dup = sorted(rules.loc[rules["sensitivity"].duplicated(keep=False), "sensitivity"].unique())
    if dup:
        raise ValueError(f"build_ast_rules: {len(dup)} string(s) listed more than once (in both lookups or repeated): {dup}")
    return rules


def label_esbl_status(samples: pd.DataFrame, results: pd.DataFrame, rules: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add the ESBL status to each sample: 1 = ESBL, 0 = non-ESBL, ``<NA>`` = unknown.

    Grain: ``samples`` has one row per sample (``columns.sample_key``), unchanged
    in rows, order and index. ``results`` has one row per raw result string
    (``sensitivity``) per sample. Positive evidence wins over negative evidence.
    CHANGE vs baseline (``label_esbl_status``): works on the raw result strings,
    matches lookups per drug, includes cefixime, counts the lab's ESBL-producer
    statement as positive, and returns 1/0/NA instead of strings.

    Raises
    ------
    ValueError
        On missing columns, duplicate sample keys, or results whose sample is not in ``samples``.
    """
    oc, key, out_col = cfg["outcome"], list(cfg["columns"]["sample_key"]), cfg["columns"]["outcome"]
    for name, df, cols in (("samples", samples, key + [oc["esbl_statement_column"]]),
                           ("results", results, key + ["sensitivity"])):
        absent = [c for c in cols if c not in df.columns]
        if absent:
            raise ValueError(f"{STEP}: {name} is missing column(s) {absent}")
    n_dup = int(samples.duplicated(key).sum())
    if n_dup:
        raise ValueError(f"{STEP}: {n_dup} duplicate sample key(s) in samples (key {key})")
    keys = samples[key].drop_duplicates()
    n_orphan = int((results[key].merge(keys, on=key, how="left", indicator=True)["_merge"] == "left_only").sum())
    if n_orphan:
        raise ValueError(f"{STEP}: {n_orphan} result row(s) belong to no sample in samples")

    r = results[key + ["sensitivity"]].copy()
    r["sensitivity"] = _tidy(r["sensitivity"])
    r = r.merge(rules, on="sensitivity", how="left", validate="many_to_one")

    prefix = oc["marker_prefix"].lower() + " : "
    marker_result = r["sensitivity"].str[len(prefix):].where(r["sensitivity"].str.startswith(prefix))
    r["resistant"] = ((r["rule"] == "resistant") & r["drug"].isin(oc["resistant_drugs"])).fillna(False).astype(bool)
    r["marker_pos"] = marker_result.isin(oc["marker_positive"]).fillna(False).astype(bool)
    r["marker_neg"] = marker_result.isin(oc["marker_negative"]).fillna(False).astype(bool)
    susceptible_drugs = sorted({d for pair in oc["susceptible_pairs"] for d in pair} | set(oc["susceptible_alone_drugs"]))
    for d in susceptible_drugs:
        r[f"s_{d}"] = ((r["rule"] == "susceptible") & (r["drug"] == d)).fillna(False).astype(bool)
    flags = ["resistant", "marker_pos", "marker_neg"] + [f"s_{d}" for d in susceptible_drugs]
    evidence = r.groupby(key)[flags].max().reset_index()

    # Left merge keeps the order of `samples`; index is restored afterwards.
    ev = samples[key].reset_index(drop=True).merge(evidence, on=key, how="left", validate="many_to_one")
    ev[flags] = ev[flags].astype("boolean").fillna(False).astype(bool)
    ev.index = samples.index

    statement = samples[oc["esbl_statement_column"]].fillna(0).astype(int) == 1
    positive = statement | ev["resistant"] | ev["marker_pos"]
    negative = ev["marker_neg"].copy()
    for d in oc["susceptible_alone_drugs"]:
        negative |= ev[f"s_{d}"]
    for a, b in oc["susceptible_pairs"]:
        negative |= ev[f"s_{a}"] & ev[f"s_{b}"]

    out = samples.copy()
    status = pd.Series(pd.NA, index=out.index, dtype="Int64")
    status[negative] = 0
    status[positive] = 1
    out[out_col] = status
    log.info("%s: %d ESBL, %d non-ESBL, %d unknown of %d samples (%d with both positive and negative evidence)", STEP,
             int((status == 1).sum()), int((status == 0).sum()), int(status.isna().sum()), len(out), int((positive & negative).sum()))
    return out
