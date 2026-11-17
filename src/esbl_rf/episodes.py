"""Assign infection episodes to samples."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "assign_infection_episodes"


def assign_infection_episodes(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Label each sample with the infection episode it belongs to.

    Grain: one row per sample (input and output). Row count, order and index
    are unchanged; ``episode_number`` and ``infection_id`` are added.

    Within a spell, samples of the same organism form one episode until the gap
    since the PREVIOUS sample exceeds ``episodes.gap_days``. CHANGE vs baseline
    (``assign_infection_ep_id.py``): the gap is compared as an exact
    ``Timedelta``; the baseline floored it to whole days, so 14 d 23 h was
    treated as 14 days and kept in the same episode (see analysis_plan section 10).

    Parameters
    ----------
    samples : pd.DataFrame
        Sample-level table with the spell, organism and collection-time columns
        named in ``config.yaml: columns``.
    cfg : dict
        Configuration from ``load_config``.

    Returns
    -------
    pd.DataFrame
        Copy of ``samples`` with ``episode_number`` (1-based within spell and
        organism) and ``infection_id`` (``spell|organism|episode_number``).

    Raises
    ------
    ValueError
        If any collection time is missing, or a required column is absent.
    """
    cols = cfg["columns"]
    spell, organism, collect = cols["spell"], cols["organism"], cols["collect"]
    absent = [c for c in (spell, organism, collect) if c not in samples.columns]
    if absent:
        raise ValueError(f"{STEP}: missing column(s) {absent}")
    n_missing = int(samples[collect].isna().sum())
    if n_missing:
        raise ValueError(f"{STEP}: {n_missing} of {len(samples)} sample(s) have no collection time ('{collect}')")

    gap = pd.Timedelta(days=cfg["episodes"]["gap_days"])
    out = samples.copy()
    # Work on a positional copy so the caller's index (even a duplicated one) is
    # never used for alignment; results go back by row position.
    work = samples[[spell, organism, collect]].reset_index(drop=True)
    work = work.sort_values([spell, organism, collect], kind="stable")
    keys = [work[spell], work[organism]]
    since_previous = work.groupby(keys, sort=False)[collect].diff()
    new_episode = since_previous.isna() | (since_previous > gap)
    number = new_episode.groupby(keys, sort=False).cumsum().sort_index()
    out["episode_number"] = number.to_numpy().astype(int)
    out["infection_id"] = out[spell].astype(str) + "|" + out[organism].astype(str) + "|" + out["episode_number"].astype(str)

    log.info("%s: %d samples -> %d episodes", STEP, len(out), out["infection_id"].nunique())
    return out
