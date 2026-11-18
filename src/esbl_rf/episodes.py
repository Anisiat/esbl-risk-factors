"""Assign infection episodes to samples."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)
STEP = "assign_infection_episodes"
AGG_STEP = "aggregate_episodes"


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


def aggregate_episodes(samples: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Collapse samples to one row per infection episode.

    Grain: input has one row per sample (``columns.sample_key``) with
    ``infection_id``; output has one row per ``infection_id``. Ported from the
    manuscript's ``build_infection_eps``; kept: status, site hierarchy, counts,
    first/last culture, spell variables. CHANGE: surgery variables (specialty,
    surgery length, ``n_surgeries``, days from surgery) come from the episode's
    first sample and its attributed surgery, not min/max/mode across samples.

    - ``esbl_status``: 1 if any sample is ESBL, 0 if any is non-ESBL and none is
      ESBL, missing if no sample has a status.
    - ``site``: first of ``sites.hierarchy`` present; ``n_sites`` counts distinct
      sites other than ``sites.other_label``. Samples without a status or from
      ``other`` sites still count for ``n_samples`` and the culture span.
    - Intervals (``culture_span_days``, ``length_of_stay_days``,
      ``days_from_admission_to_first_culture``) are whole days (floored), as in
      the manuscript.

    Raises
    ------
    ValueError
        On missing columns or collection times, duplicate samples, a spell value
        that differs within an episode, more than one organism in an episode, or
        a site not in the hierarchy.
    """
    cols, et, sites = cfg["columns"], cfg["episode_table"], cfg["sites"]
    ep, collect, organism = "infection_id", cols["collect"], cols["organism"]
    site_col, outcome = cols["site_group"], cols["outcome"]
    key = list(cols["sample_key"])
    spell_map, index_map = et["spell_columns"], et["index_sample_columns"]
    needed = {ep, collect, organism, site_col, outcome, cols["surgery_start"], cols["surgery_stop"], *key,
              *spell_map.values(), *index_map.values()}
    absent = sorted(needed - set(samples.columns))
    if absent:
        raise ValueError(f"{AGG_STEP}: missing column(s) {absent}")
    n_missing = int(samples[collect].isna().sum())
    if n_missing:
        raise ValueError(f"{AGG_STEP}: {n_missing} of {len(samples)} sample(s) have no collection time ('{collect}')")
    n_dup = int(samples.duplicated(key).sum())
    if n_dup:
        raise ValueError(f"{AGG_STEP}: {n_dup} duplicate sample(s) on key {key}")
    unknown_sites = sorted(set(samples[site_col].dropna()) - set(sites["hierarchy"]))
    if unknown_sites:
        raise ValueError(f"{AGG_STEP}: site(s) {unknown_sites} are not in sites.hierarchy")

    df = samples.sort_values([ep, collect], kind="stable")
    grouped = df.groupby(ep, sort=True)

    for label, mapping in (("spell", spell_map), ("organism", {organism: organism})):
        for out_name, source in mapping.items():
            n_differ = int((grouped[source].nunique(dropna=False) > 1).sum())
            if n_differ:
                raise ValueError(f"{AGG_STEP}: {label} column '{source}' differs within {n_differ} episode(s)")

    first = grouped.head(1).set_index(ep)
    out = pd.DataFrame(index=first.index)
    for out_name, source in spell_map.items():
        out[out_name] = first[source]
    out[organism] = first[organism]

    rank = {label: i for i, label in enumerate(sites["hierarchy"])}
    site_rank = df[site_col].map(rank)
    best = df.assign(_rank=site_rank).dropna(subset=["_rank"]).groupby(ep)["_rank"].min()
    labels = pd.Series(sites["hierarchy"])
    out["site"] = best.map(lambda r: labels.iloc[int(r)]).reindex(out.index)
    real_sites = df[df[site_col] != sites["other_label"]]
    out["n_sites"] = real_sites.groupby(ep)[site_col].nunique().reindex(out.index).fillna(0).astype(int)
    out["n_samples"] = grouped.size()
    out[outcome] = grouped[outcome].max()
    out["first_culture_dt"] = grouped[collect].min()
    out["last_culture_dt"] = grouped[collect].max()

    for out_name, source in index_map.items():
        out[out_name] = first[source]
    out["surgery_length"] = (first[cols["surgery_stop"]] - first[cols["surgery_start"]]).dt.total_seconds() / 3600

    out["culture_span_days"] = (out["last_culture_dt"] - out["first_culture_dt"]).dt.days
    out["length_of_stay_days"] = (out["discharge_date"] - out["admission_date"]).dt.days
    out["days_from_admission_to_first_culture"] = (out["first_culture_dt"] - out["admission_date"]).dt.days

    result = out.reset_index()
    assert result[ep].is_unique, f"{AGG_STEP}: infection_id is not unique after aggregation"
    log.info("%s: %d samples -> %d episodes", AGG_STEP, len(samples), len(result))
    return result


def exclude_episodes_without_status(episodes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Drop episodes in which no sample gave an ESBL status (grain: one row per episode)."""
    outcome = cfg["columns"]["outcome"]
    if outcome not in episodes.columns:
        raise ValueError(f"exclude_episodes_without_status: missing column '{outcome}'")
    kept = episodes[episodes[outcome].notna()].copy()
    log.info("exclude_episodes_without_status: dropped %d of %d episodes", len(episodes) - len(kept), len(episodes))
    return kept


def exclude_other_site_episodes(episodes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Drop episodes whose resolved site is ``sites.other_label`` (grain: one row per episode)."""
    if "site" not in episodes.columns:
        raise ValueError("exclude_other_site_episodes: missing column 'site'")
    kept = episodes[episodes["site"] != cfg["sites"]["other_label"]].copy()
    log.info("exclude_other_site_episodes: dropped %d of %d episodes", len(episodes) - len(kept), len(episodes))
    return kept
