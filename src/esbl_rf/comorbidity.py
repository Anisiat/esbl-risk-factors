"""Charlson comorbidities and CCI score via ``comorbidicare`` (analysis_plan section 7)."""
from __future__ import annotations

import logging

import pandas as pd
from comorbidicare import build_comorbidity_table

log = logging.getLogger(__name__)
STEP = "add_comorbidities"
ADMISSION, DISCHARGE = "admission_date", "discharge_date"   # episode-table column names


def add_comorbidities(episodes: pd.DataFrame, diagnoses: pd.DataFrame, prescriptions: pd.DataFrame,
                      problems: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add the 17 Charlson flags, ``cci_score`` and ``cci_group`` to each episode.

    Grain: ``episodes`` has one row per infection episode (unchanged). The
    evidence tables are raw records (diagnoses, prescriptions, problems). Flags
    come from ``comorbidicare.build_comorbidity_table`` run once per spell, using only
    evidence dated strictly before ``cci.cutoff`` (admission); every episode of a
    spell gets the spell's values. No evidence means 0 (comorbidicare fills it). ``cci_group`` bins the score
    with ``cci.group_edges`` (left-closed) and ``cci.group_labels``.
    CHANGE vs baseline (``infer_comorbidities``): replaces the hand-written drug lists and
    the manuscript's individual comorbidities.

    Input checks (evidence present, one cutoff per spell, required columns, one
    output row per spell) are left to comorbidicare, which raises on each.

    Raises
    ------
    ValueError
        If ``episodes`` already has an output column, or a Charlson flag named in
        ``config.yaml: table1.supplementary_conditions`` is not one comorbidicare returns.
    """
    cols, cci = cfg["columns"], cfg["cci"]
    subject, spell = cols["subject"], cols["spell"]
    conditions = list(cfg["table1"]["supplementary_conditions"])
    out_cols = conditions + ["cci_score", "cci_group"]

    clash = [c for c in out_cols if c in episodes.columns]
    if clash:
        raise ValueError(f"{STEP}: episodes already has column(s) {clash}")

    cohort = episodes[[subject, spell, ADMISSION, DISCHARGE]]
    table = build_comorbidity_table(cohort, diagnoses_df=diagnoses, prescriptions_df=prescriptions,
                                    problems_df=problems, cci_score=True, cutoff=cci["cutoff"])

    missing_flags = [c for c in conditions if c not in table.columns]
    if missing_flags:
        raise ValueError(f"{STEP}: comorbidicare did not return column(s) {missing_flags}; check config.yaml: table1.supplementary_conditions")

    out = episodes.merge(table[[subject, spell, *conditions, "cci_score"]], on=[subject, spell], how="left", validate="many_to_one")
    edges = list(cci["group_edges"]) + [float("inf")]
    out["cci_group"] = pd.cut(out["cci_score"], bins=edges, labels=cci["group_labels"], right=False).astype("string")
    log.info("%s: CCI group counts %s", STEP, out["cci_group"].value_counts().to_dict())
    return out
