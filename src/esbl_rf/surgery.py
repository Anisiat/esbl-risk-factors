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
