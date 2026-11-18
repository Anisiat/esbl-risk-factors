"""Group free-text specimen sites into clinical categories."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "group_specimen_site"


def _clean(text: pd.Series) -> pd.Series:
    """Lower-case, turn ``-`` and ``/`` into spaces and collapse repeated spaces."""
    return (text.astype("string").str.lower().str.replace("-", " ", regex=False)
            .str.replace("/", " ", regex=False).str.replace(r"\s+", " ", regex=True).str.strip())


def group_specimen_site(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``site_grouped``, a clinical category for each sample's specimen site.

    Grain: one row per sample (unchanged). Site and culture type are cleaned and
    joined into one text; the first group in ``config.yaml: sites.groups`` whose
    regular expression matches wins, and anything else is ``sites.other_label``.
    Ported from the manuscript's ``group_specimen_site`` with the patterns moved
    to config (and one redundant pattern removed).

    Raises
    ------
    ValueError
        If the site or culture-type column is missing.
    """
    cols, sites = cfg["columns"], cfg["sites"]
    absent = [c for c in (cols["site"], cols["culture_type"]) if c not in samples.columns]
    if absent:
        raise ValueError(f"{STEP}: missing column(s) {absent}")

    text = (_clean(samples[cols["site"]]).fillna("") + " " + _clean(samples[cols["culture_type"]]).fillna("")).str.strip()
    grouped = pd.Series(pd.NA, index=samples.index, dtype="string")
    for group in sites["groups"]:
        matches = text.str.contains(group["pattern"], regex=True, na=False)
        grouped = grouped.where(grouped.notna() | ~matches, group["label"])
    out = samples.copy()
    out[cols["site_group"]] = grouped.fillna(sites["other_label"])
    log.info("%s: %s", STEP, out[cols["site_group"]].value_counts().to_dict())
    return out
