"""ESBL labelling of samples (analysis_plan section 6)."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "label_esbl_status"


def _normalise(text: pd.Series) -> pd.Series:
    """Lower-case and tidy spacing around the colon: ``'Drug:X'`` -> ``'drug : x'``."""
    parts = text.astype("string").str.lower().str.split(":", n=1, expand=True)
    return parts[0].str.strip() + " : " + parts[1].str.strip()


def _rules(df: pd.DataFrame, which: str) -> set[str]:
    """Normalised ``'drug : result'`` strings from one lookup table (column ``sensitivity``)."""
    strings = df["sensitivity"].astype("string").str.lower()
    bad = strings[~strings.str.contains(":", regex=False)]
    if not bad.empty:
        raise ValueError(f"build_ast_rules: {len(bad)} {which} lookup string(s) have no ':' (e.g. {bad.iloc[0]!r})")
    return set(_normalise(strings).dropna())


def build_ast_rules(resistant: pd.DataFrame, susceptible: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Turn the 3GCR and 3GCS lookup tables into sets of ``'drug : result'`` strings.

    Grain of the inputs: one row per distinct ``sensitivity`` string. CHANGE vs
    baseline (``get_ast_rules``): the drug is kept, so a result is matched for
    the right antibiotic; the baseline kept only the result part.

    Raises
    ------
    ValueError
        If a string has no ``':'`` or appears in both lookups.
    """
    r_rules, s_rules = _rules(resistant, "resistant"), _rules(susceptible, "susceptible")
    both = sorted(r_rules & s_rules)
    if both:
        raise ValueError(f"build_ast_rules: {len(both)} string(s) in both resistant and susceptible lookups: {both}")
    return r_rules, s_rules


def label_esbl_status(samples: pd.DataFrame, r_rules: set[str], s_rules: set[str], cfg: dict) -> pd.DataFrame:
    """Add ``esbl_status`` to each sample: 1 = ESBL, 0 = non-ESBL, ``<NA>`` = unknown.

    Grain: one row per sample (unchanged). Positive evidence wins over
    negative evidence. CHANGE vs baseline (``label_esbl_status``): vectorised,
    outputs 1/0/NA instead of strings, matches lookups per drug, and counts the
    lab's ESBL-producer statement as positive (analysis_plan section 6).

    Raises
    ------
    ValueError
        If a required susceptibility column is missing.
    """
    oc = cfg["outcome"]
    drugs = list(oc["third_gen_columns"]) + [oc["cefpodoxime_column"]]
    needed = drugs + [oc["marker_column"], oc["esbl_statement_column"]]
    absent = [c for c in needed if c not in samples.columns]
    if absent:
        raise ValueError(f"{STEP}: missing column(s) {absent}")

    def in_rules(drug: str, rules: set[str]) -> pd.Series:
        result = samples[drug].astype("string").str.lower().str.strip()
        return (drug + " : " + result).isin(rules).fillna(False).astype(bool)

    resistant = {d: in_rules(d, r_rules) for d in drugs}
    susceptible = {d: in_rules(d, s_rules) for d in drugs}
    marker = samples[oc["marker_column"]].astype("string").str.lower().str.strip()
    statement = samples[oc["esbl_statement_column"]].fillna(0).astype(int) == 1

    positive = statement | marker.isin(oc["marker_positive"]).fillna(False).astype(bool)
    for d in drugs:
        positive |= resistant[d]
    negative = susceptible[oc["cefpodoxime_column"]] | marker.isin(oc["marker_negative"]).fillna(False).astype(bool)
    for a, b in oc["susceptible_pairs"]:
        negative |= susceptible[a] & susceptible[b]

    out = samples.copy()
    status = pd.Series(pd.NA, index=out.index, dtype="Int64")
    status[negative] = 0
    status[positive] = 1
    out[cfg["columns"]["outcome"]] = status
    log.info("%s: %d ESBL, %d non-ESBL, %d unknown of %d samples", STEP,
             int((status == 1).sum()), int((status == 0).sum()), int(status.isna().sum()), len(out))
    return out
