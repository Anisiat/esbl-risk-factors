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
    spell gets the spell's values. No evidence means 0. ``cci_group`` bins the score
    with ``cci.group_edges`` (left-closed) and ``cci.group_labels``.
    CHANGE vs baseline (``infer_comorbidities``): replaces the hand-written drug lists and
    the manuscript's individual comorbidities.

    Raises
    ------
    ValueError
        On missing columns, a column that already exists in ``episodes``, a spell
        with conflicting admission/discharge dates, or a Charlson flag that
        comorbidicare did not return.
    """
    cols, cci = cfg["columns"], cfg["cci"]
    subject, spell = cols["subject"], cols["spell"]
    conditions = list(cfg["table1"]["supplementary_conditions"])
    out_cols = conditions + ["cci_score", "cci_group"]

    needed = [subject, spell, ADMISSION, DISCHARGE]
    absent = [c for c in needed if c not in episodes.columns]
    if absent:
        raise ValueError(f"{STEP}: episodes is missing column(s) {absent}")
    clash = [c for c in out_cols if c in episodes.columns]
    if clash:
        raise ValueError(f"{STEP}: episodes already has column(s) {clash}")

    cohort = episodes[needed].drop_duplicates()
    n_conflict = int(cohort.duplicated([subject, spell]).sum())
    if n_conflict:
        raise ValueError(f"{STEP}: {n_conflict} spell(s) have more than one admission/discharge date across episodes")

    sources = {"diagnoses_df": diagnoses, "prescriptions_df": prescriptions, "problems_df": problems}
    kwargs = {k: (v if len(v) else None) for k, v in sources.items()}   # comorbidicare wants None, not an empty table
    if all(v is None for v in kwargs.values()):
        raise ValueError(f"{STEP}: no diagnoses, prescriptions or problems records")
    table = build_comorbidity_table(cohort, cci_score=True, cutoff=cci["cutoff"], **kwargs)

    missing_flags = [c for c in conditions + ["cci_score"] if c not in table.columns]
    if missing_flags:
        raise ValueError(f"{STEP}: comorbidicare did not return column(s) {missing_flags}; check config.yaml: table1.supplementary_conditions")
    if int(table.duplicated([subject, spell]).sum()):
        raise ValueError(f"{STEP}: comorbidicare returned more than one row for a spell")

    out = episodes.merge(table[[subject, spell, *conditions, "cci_score"]], on=[subject, spell], how="left", validate="many_to_one")
    n_none = int(out["cci_score"].isna().sum())
    if n_none:
        log.warning("%s: %d of %d episode(s) have no comorbidicare row; flags set to 0", STEP, n_none, len(out))
    out[conditions + ["cci_score"]] = out[conditions + ["cci_score"]].fillna(0)
    out[conditions] = out[conditions].astype(int)
    edges = list(cci["group_edges"]) + [float("inf")]
    out["cci_group"] = pd.cut(out["cci_score"], bins=edges, labels=cci["group_labels"], right=False).astype("string")
    if len(out) != len(episodes):
        raise ValueError(f"{STEP}: row count changed from {len(episodes)} to {len(out)}")
    log.info("%s: CCI group counts %s", STEP, out["cci_group"].value_counts().to_dict())
    return out
