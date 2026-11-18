"""Attribute each sample to the surgery that preceded it."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "attribute_surgery"


def attribute_surgery(samples: pd.DataFrame, surgeries: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Copy the most recent preceding surgery onto each sample.

    Grain: ``samples`` has one row per sample; ``surgeries`` has one row per
    surgery in a spell. The output has exactly the rows, order and index of
    ``samples``. A sample is attributed to the surgery in the same spell with the
    latest anchor time strictly before the sample's collection time (the
    manuscript took the first surgery per spell; analysis_plan section 2). Samples
    with no preceding surgery get missing values (they are preoperative).

    Parameters
    ----------
    samples : pd.DataFrame
        Needs the subject, spell and collection-time columns (``config.yaml: columns``).
    surgeries : pd.DataFrame
        Needs subject, spell and the ``columns.surgery_columns`` columns.
    cfg : dict
        Configuration from ``load_config``.

    Returns
    -------
    pd.DataFrame
        ``samples`` plus the ``surgery_columns`` columns, ``n_surgeries_before``
        (surgeries in the spell before the sample) and
        ``days_from_surgery_to_sample``.

    Raises
    ------
    ValueError
        On missing columns, a surgery column that already exists in ``samples``,
        missing collection times, or duplicate surgery rows.
    """
    cols = cfg["columns"]
    subject, spell, collect, anchor = cols["subject"], cols["spell"], cols["collect"], cols["surgery_anchor"]
    surgery_cols = list(cols["surgery_columns"])

    for name, df, needed in (("samples", samples, [subject, spell, collect]),
                             ("surgeries", surgeries, [subject, spell, *surgery_cols])):
        absent = [c for c in needed if c not in df.columns]
        if absent:
            raise ValueError(f"{STEP}: {name} is missing column(s) {absent}")
    clash = [c for c in surgery_cols + ["n_surgeries_before", "days_from_surgery_to_sample"] if c in samples.columns]
    if clash:
        raise ValueError(f"{STEP}: samples already has column(s) {clash}")
    n_missing = int(samples[collect].isna().sum())
    if n_missing:
        raise ValueError(f"{STEP}: {n_missing} of {len(samples)} sample(s) have no collection time ('{collect}')")
    n_dup = int(surgeries.duplicated([subject, spell, *surgery_cols]).sum())
    if n_dup:
        raise ValueError(f"{STEP}: {n_dup} duplicate surgery row(s) in surgeries")

    work = samples[[subject, spell, collect]].reset_index(drop=True).rename_axis("_row").reset_index()
    pairs = work.merge(surgeries[[subject, spell, *surgery_cols]], on=[subject, spell], how="inner")
    pairs = pairs[pairs[anchor] < pairs[collect]]
    n_before = pairs.groupby("_row").size()
    latest = pairs.sort_values(anchor, kind="stable").groupby("_row").tail(1)
    attributed = work[["_row"]].merge(latest[["_row", *surgery_cols]], on="_row", how="left", validate="one_to_one")

    out = samples.copy()
    for col in surgery_cols:
        out[col] = attributed[col].to_numpy()
    out["n_surgeries_before"] = work["_row"].map(n_before).fillna(0).astype(int).to_numpy()
    out["days_from_surgery_to_sample"] = ((samples[collect].to_numpy() - attributed[anchor].to_numpy())
                                          / pd.Timedelta(days=1))
    log.info("%s: %d of %d samples have a preceding surgery", STEP, int(attributed[anchor].notna().sum()), len(out))
    return out


def clean_surgeries(surgeries: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Drop generic procedure labels where the spell has a real procedure.

    Grain: one row per surgery in a spell (rows only removed; order and index kept).
    Ported from the manuscript's ``get_surgery_features``: labels in
    ``surgery.meta_procedure_labels`` are dropped from a spell only if the spell
    also has a surgery whose label is not one of them (a missing label counts as
    real), so spells with only generic labels keep them. CHANGE: judged per spell,
    not per infection episode, and emergency labels are not handled here because
    ``02_cohort.sql`` already removes them.

    Raises
    ------
    ValueError
        If the subject, spell or procedure column is missing, or any procedure
        label contains ``emergency`` (the SQL filter should have removed it).
    """
    cols = cfg["columns"]
    subject, spell, procedure = cols["subject"], cols["spell"], cols["procedure"]
    absent = [c for c in (subject, spell, procedure) if c not in surgeries.columns]
    if absent:
        raise ValueError(f"clean_surgeries: missing column(s) {absent}")

    label = surgeries[procedure].astype("string").str.strip().str.lower()
    n_emergency = int(label.str.contains("emergency", regex=False).fillna(False).sum())
    if n_emergency:
        raise ValueError(f"clean_surgeries: {n_emergency} surgery row(s) have an 'emergency' procedure label; "
                         "02_cohort.sql should have excluded them")

    is_meta = label.isin([m.lower() for m in cfg["surgery"]["meta_procedure_labels"]]).fillna(False).astype(bool)
    spell_has_real = (~is_meta).groupby([surgeries[subject], surgeries[spell]]).transform("any")
    kept = surgeries[~(is_meta & spell_has_real)].copy()
    log.info("clean_surgeries: dropped %d of %d generic-label surgery rows", len(surgeries) - len(kept), len(surgeries))
    return kept


def map_specialty(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``tfc_group``: the specialty group of the attributed surgery's consultant episode.

    Grain: one row per sample (unchanged). ``treatment_function_code_des`` is
    stripped and lower-cased, then mapped with ``config.yaml: tfc_groups``. Values
    not listed (and missing values) stay missing; the unmapped values are logged
    with counts so a spelling difference shows up on the first real run.
    """
    cols = cfg["columns"]
    if cols["tfc_desc"] not in samples.columns:
        raise ValueError(f"map_specialty: missing column '{cols['tfc_desc']}'")
    desc = samples[cols["tfc_desc"]].astype("string").str.strip().str.lower()
    out = samples.copy()
    out[cols["tfc_group"]] = desc.map(cfg["tfc_groups"])
    unmapped = desc[out[cols["tfc_group"]].isna() & desc.notna()].value_counts()
    n_missing = int(desc.isna().sum())
    if len(unmapped) or n_missing:
        log.warning("map_specialty: %d unmapped value(s) %s; %d missing", len(unmapped), unmapped.to_dict(), n_missing)
    return out
