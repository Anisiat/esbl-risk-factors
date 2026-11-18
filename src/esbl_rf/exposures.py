"""Exposures before admission: prior inpatient stay and prior ESBL-positive culture."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
ADMISSION = "admission_date"   # episode-table column (config.yaml: episode_table.spell_columns)
EPISODE = "infection_id"


def _require(df: pd.DataFrame, cols: list[str], what: str, step: str) -> None:
    absent = [c for c in cols if c not in df.columns]
    if absent:
        raise ValueError(f"{step}: {what} is missing column(s) {absent}")


def flag_prior_inpatient(episodes: pd.DataFrame, prior: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``inpatient_exposure_<N>d``: 1 if an earlier stay overlaps the N days before admission.

    Grain: ``episodes`` has one row per infection episode (unchanged);
    ``prior`` has one row per (index spell, prior spell), already limited to the
    window by ``AT_PRIOR_ADMISSIONS``. No row = no recorded exposure (0).
    CHANGE vs baseline (``get_healthcare_exposure``): keyed on the spell, not the
    admission date; the window is checked, not recomputed; emergency, outpatient
    and combined flags are dropped.

    Raises
    ------
    ValueError
        On missing columns, a prior spell equal to the index spell, or rows outside
        ``exposures.lookback_days`` (the SQL and config disagree).
    """
    step = "flag_prior_inpatient"
    cols, ec = cfg["columns"], cfg["exposures"]["inpatient_columns"]
    subject, spell, days = cols["subject"], cols["spell"], cfg["exposures"]["lookback_days"]
    out_col = f"inpatient_exposure_{days}d"
    _require(prior, [subject, spell, "prior_spell_identifier", *ec.values()], "prior admissions", step)
    _require(episodes, [subject, spell], "episodes", step)

    same = int((prior["prior_spell_identifier"] == prior[spell]).sum())
    if same:
        raise ValueError(f"{step}: {same} row(s) where the prior spell is the index spell")
    ia, pa, pd_ = (prior[ec[k]] for k in ("index_admission", "prior_admission", "prior_discharge"))
    window_start = ia - pd.Timedelta(days=days)
    outside = ~((pa < ia) & (pd_.fillna(pa) >= window_start))
    if outside.any():
        raise ValueError(f"{step}: {int(outside.sum())} prior admission row(s) outside the {days}-day window "
                         "(starts before admission, ends within the window); check lookback_days against 00_params.sql")

    exposed = prior[[subject, spell]].drop_duplicates().assign(**{out_col: 1})
    out = episodes.merge(exposed, on=[subject, spell], how="left", validate="many_to_one")
    out[out_col] = out[out_col].fillna(0).astype("Int64")
    if len(out) != len(episodes):
        raise ValueError(f"{step}: row count changed from {len(episodes)} to {len(out)}")
    log.info("%s: %d of %d episodes with an inpatient stay in the %d days before admission", step,
             int(out[out_col].sum()), len(out), days)
    return out


def flag_prior_esbl(episodes: pd.DataFrame, samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``prior_positive_esbl``: 1 if an ESBL-positive sample was collected before admission.

    Grain: ``episodes`` has one row per infection episode (unchanged); ``samples``
    has one row per sample (all time, clinical and screening, already labelled).
    Only samples with status 1 and a collection time strictly before the episode's
    admission count; unknown status is not positive. The lookback is
    ``exposures.prior_esbl_lookback_days`` (null = any time).
    CHANGE vs baseline (``get_prior_esbl_culture``): reuses the labels from the
    labelling step instead of labelling again, and merges only the positive
    samples (a far smaller join).

    Raises
    ------
    ValueError
        On missing columns.
    """
    step = "flag_prior_esbl"
    cols = cfg["columns"]
    subject, collect, status = cols["subject"], cols["collect"], cols["outcome"]
    _require(samples, [subject, collect, status], "samples", step)
    _require(episodes, [subject, EPISODE, ADMISSION], "episodes", step)
    lookback = cfg["exposures"]["prior_esbl_lookback_days"]

    positives = samples.loc[(samples[status] == 1) & samples[collect].notna(), [subject, collect]]
    pairs = episodes[[EPISODE, subject, ADMISSION]].merge(positives, on=subject, how="inner")
    hit = pairs[collect] < pairs[ADMISSION]
    if lookback is not None:
        hit &= pairs[collect] >= pairs[ADMISSION] - pd.Timedelta(days=lookback)
    flagged = set(pairs.loc[hit, EPISODE])

    out = episodes.copy()
    out["prior_positive_esbl"] = out[EPISODE].isin(flagged).astype("Int64")
    log.info("%s: %d of %d episodes with an ESBL-positive sample before admission (lookback %s)", step,
             int(out["prior_positive_esbl"].sum()), len(out), "any time" if lookback is None else f"{lookback} d")
    return out
