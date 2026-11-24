"""Demographics: ethnicity and sex (analysis_plan sections 7 and 10, item 18)."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "clean_demographics"
ETHNICITY, GENDER = "ethnicity_desc", "gender"


def _group(raw: pd.Series, mapping: dict[str, str], what: str) -> pd.Series:
    """Map lower-cased, trimmed text to a group; values not in ``mapping`` become missing and are logged."""
    text = raw.astype("string").str.strip().str.lower()
    grouped = text.map(mapping).astype("string")
    unmapped = text[text.notna() & ~text.isin(list(mapping))].value_counts()   # a null value in config = deliberately missing
    if not unmapped.empty:
        log.warning("%s: %d %s value(s) not in config.yaml: demographics and set to missing: %s", STEP,
                    int(unmapped.sum()), what, unmapped.to_dict())
    return grouped


def clean_demographics(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Group ethnicity and sex.

    Grain: one row per patient (``subject``), input and output.
    CHANGE vs baseline (``clean_demographics.run``): no ethnicity code lookup
    (the SQL exports ``ethnicity_desc``); the three patient-specific overrides are
    removed; groups come from ``config.yaml: demographics``; values missing from
    that list are logged instead of passing through unchanged; ``subject`` must be unique.

    Returns
    -------
    pd.DataFrame
        Columns ``subject``, ``ethnicity_desc`` (group), ``gender`` (``female`` /
        ``male`` / missing) and ``death_date``.

    Raises
    ------
    ValueError
        On missing columns or a repeated ``subject``.
    """
    subject = cfg["columns"]["subject"]
    needed = list(cfg["columns"]["demographics"])
    absent = [c for c in needed if c not in raw.columns]
    if absent:
        raise ValueError(f"{STEP}: missing column(s) {absent}")
    n_dup = int(raw[subject].duplicated().sum())
    if n_dup:
        raise ValueError(f"{STEP}: {n_dup} duplicate subject row(s); expected one row per patient")

    dem = cfg["demographics"]
    out = pd.DataFrame({
        subject: raw[subject],
        ETHNICITY: _group(raw["ethnicity_desc"], dem["ethnicity_groups"], "ethnicity"),
        GENDER: _group(raw["gender_desc"], dem["gender_groups"], "gender"),
        "death_date": raw["death_date"],
    }).reset_index(drop=True)
    log.info("%s: %d patients; ethnicity %s; gender %s", STEP, len(out),
             out[ETHNICITY].value_counts(dropna=False).to_dict(), out[GENDER].value_counts(dropna=False).to_dict())
    return out


def attach_demographics(episodes: pd.DataFrame, demographics: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ethnicity, sex and death date to each episode.

    Grain: ``episodes`` has one row per infection episode (unchanged);
    ``demographics`` one row per patient. Patients with no demographics row get missing values.

    Raises
    ------
    ValueError
        If a column to be added already exists in ``episodes``, or the row count changes.
    """
    subject = cfg["columns"]["subject"]
    added = [c for c in demographics.columns if c != subject]
    clash = [c for c in added if c in episodes.columns]
    if clash:
        raise ValueError(f"attach_demographics: column(s) {clash} already in episodes")
    out = episodes.merge(demographics, on=subject, how="left", validate="many_to_one")
    if len(out) != len(episodes):
        raise ValueError(f"attach_demographics: row count changed from {len(episodes)} to {len(out)}")
    n_none = int(out[GENDER].isna().sum())
    log.info("attach_demographics: %d of %d episodes have no sex recorded", n_none, len(out))
    return out


def merge_sparse_ethnicity(episodes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Merge ethnicity groups with fewer than ``ethnicity_min_count`` episodes into ``ethnicity_sparse_into``.

    Grain: one row per infection episode (unchanged). Counted over the analysed
    episodes. CHANGE vs earlier port (decided 2026-11-23): sparse groups (e.g.
    Mixed, if very few) are merged into Other instead of set to missing, so their
    episodes stay in the complete-case models. Groups at or above the threshold,
    Mixed included, are kept as they are.
    """
    dem = cfg["demographics"]
    min_count, into = dem["ethnicity_min_count"], dem["ethnicity_sparse_into"]
    counts = episodes[ETHNICITY].value_counts()
    sparse = [g for g in counts[counts < min_count].index if g != into]
    out = episodes.copy()
    out.loc[out[ETHNICITY].isin(sparse), ETHNICITY] = into
    if sparse:
        log.info("merge_sparse_ethnicity: group(s) below %d episodes merged into '%s': %s", min_count, into,
                 counts[sparse].to_dict())
    return out
