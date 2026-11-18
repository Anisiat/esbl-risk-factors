"""Screening exclusion and sample eligibility (analysis_plan section 2, steps 4-5)."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def exclude_screening_samples(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Drop non-diagnostic screening samples.

    Grain: one row per sample (unchanged apart from dropped rows). A sample is a
    screen if its lab culture code is in ``sample_eligibility.screening.culture_codes``
    or its site is in ``rectal_sites`` (lower-cased, trimmed). Ported from the
    manuscript's ``split_microbiology_screens``; the code and site lists moved to config.
    """
    cols, screening = cfg["columns"], cfg["sample_eligibility"]["screening"]
    absent = [c for c in (cols["culture_code"], cols["site"]) if c not in samples.columns]
    if absent:
        raise ValueError(f"exclude_screening_samples: missing column(s) {absent}")

    def norm(col: str) -> pd.Series:
        return samples[col].astype("string").str.strip().str.lower()

    is_screen = (norm(cols["culture_code"]).isin([c.lower() for c in screening["culture_codes"]])
                 | norm(cols["site"]).isin([s.lower() for s in screening["rectal_sites"]])).fillna(False).astype(bool)
    log.info("exclude_screening_samples: %d of %d samples are screens", int(is_screen.sum()), len(samples))
    return samples[~is_screen].copy()


def eligibility_flags(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One true/false column per eligibility rule, so each rule can be counted separately.

    Grain and index: same as ``samples``. Needs the admission and collection times
    and the attributed surgery anchor (from ``attribute_surgery``).

    Returns
    -------
    pd.DataFrame
        Boolean columns ``postoperative`` (a surgery precedes the sample),
        ``at_least_min_hours_after_admission`` and ``within_max_days_after_surgery``
        (thresholds from ``sample_eligibility``; both limits inclusive).

    Raises
    ------
    ValueError
        If a required column is missing or the admission or collection time is missing.
    """
    cols, rules = cfg["columns"], cfg["sample_eligibility"]
    admission, collect, anchor = cols["admission"], cols["collect"], cols["surgery_anchor"]
    absent = [c for c in (admission, collect, anchor) if c not in samples.columns]
    if absent:
        raise ValueError(f"eligibility_flags: missing column(s) {absent}")
    for col in (admission, collect):
        n_missing = int(samples[col].isna().sum())
        if n_missing:
            raise ValueError(f"eligibility_flags: column '{col}' is missing for {n_missing} of {len(samples)} sample(s)")

    flags = pd.DataFrame(index=samples.index)
    flags["postoperative"] = samples[anchor].notna()
    flags["at_least_min_hours_after_admission"] = (
        (samples[collect] - samples[admission]) >= pd.Timedelta(hours=rules["min_hours_after_admission"]))
    flags["within_max_days_after_surgery"] = (
        (samples[collect] - samples[anchor]) <= pd.Timedelta(days=rules["max_days_after_surgery"])).fillna(False)
    return flags.astype(bool)


def filter_eligible_samples(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Keep samples that meet every rule in ``eligibility_flags``.

    Replaces the manuscript's ``filter_nosocomial``. CHANGE: no limit relative to
    discharge; instead at most ``max_days_after_surgery`` after the most recent
    preceding surgery, and the sample must be postoperative (analysis_plan
    section 10, item 7). Grain: one row per sample.
    """
    flags = eligibility_flags(samples, cfg)
    kept = samples[flags.all(axis=1)].copy()
    log.info("filter_eligible_samples: kept %d of %d samples (%s)", len(kept), len(samples),
             ", ".join(f"{c}: {int((~flags[c]).sum())} fail" for c in flags.columns))
    return kept
