"""Pipeline steps: each reads its input, calls the pure functions, logs counts and writes a checkpoint.

A step is plumbing only (no analysis logic): ``run_<step>(cfg) -> DataFrame`` reads
the previous checkpoint (or raw CSVs), calls functions from the other modules,
records counts, asserts its own invariants and writes ``data/interim/<step>.csv``.
``scripts/run_all.py`` runs the steps in order.
"""
from __future__ import annotations

import logging

import pandas as pd

from esbl_rf.cohort_derivation import record_step
from esbl_rf.comorbidity import add_comorbidities
from esbl_rf.demographics import attach_demographics, clean_demographics, set_sparse_ethnicity_missing
from esbl_rf.eligibility import eligibility_flags, exclude_screening_samples
from esbl_rf.episodes import (aggregate_episodes, assign_infection_episodes, exclude_episodes_without_status,
                              exclude_other_site_episodes)
from esbl_rf.exposures import flag_prior_esbl, flag_prior_inpatient
from esbl_rf.io import read_checkpoint, read_lookup, read_raw, write_checkpoint
from esbl_rf.outcome import build_ast_rules, label_esbl_status
from esbl_rf.sites import group_specimen_site
from esbl_rf.surgery import add_surgery_length, attribute_surgery, clean_surgeries, map_specialty

log = logging.getLogger(__name__)

LABELLED, ELIGIBLE, EPISODES, EXPOSURES = "microbiology_labelled", "eligible_samples", "episodes", "episode_exposures"
COMORBIDITY = "episode_comorbidity"
EPISODE_DATES = ["admission_date", "discharge_date", "surgery_start_dt", "surgery_stop_dt", "first_culture_dt", "last_culture_dt"]


def _status_as_int(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """CSV turns a 1/0/missing column into floats; restore nullable integers."""
    col = cfg["columns"]["outcome"]
    df[col] = df[col].astype("Int64")
    return df


def run_label(cfg: dict) -> pd.DataFrame:
    """Label every sample in ``microbiology_ekp.csv`` (all time, clinical and screening) with its ESBL status.

    Grain: one row per sample (lab test x organism), as in the raw table.
    """
    cols = cfg["columns"]
    samples = read_raw(cfg, "microbiology_ekp", date_cols=[cols["collect"]])
    results = read_raw(cfg, "microbiology_results")
    rules = build_ast_rules(read_lookup(cfg, "third_gen_ceph_resistant"), read_lookup(cfg, "third_gen_ceph_susceptible"))
    out = label_esbl_status(samples, results, rules, cfg)
    assert len(out) == len(samples), f"{LABELLED}: row count changed"
    write_checkpoint(out, cfg, LABELLED)
    return out


def run_cohort_samples(cfg: dict) -> pd.DataFrame:
    """Build the eligible sample table (analysis_plan section 2, steps 3 onwards).

    Grain: one row per sample (spell x lab test x organism); every exclusion is
    recorded in the cohort derivation log.
    """
    cols, status_col = cfg["columns"], cfg["columns"]["outcome"]
    key = list(cols["sample_key"])
    samples = read_raw(cfg, "spell_microbiology", date_cols=[cols["collect"], cols["admission"], cols["discharge"]])
    surgeries = read_raw(cfg, "surgery_cohort", date_cols=[cols["surgery_anchor"], cols["surgery_start"], cols["surgery_stop"]])
    labelled = read_checkpoint(cfg, LABELLED)[key + [status_col]]
    labelled = _status_as_int(labelled, cfg)

    merged = samples.merge(labelled, on=key, how="left", validate="one_to_one", indicator=True)
    n_unlabelled = int((merged.pop("_merge") == "left_only").sum())
    if n_unlabelled:
        raise ValueError(f"run_cohort_samples: {n_unlabelled} spell sample(s) are not in {LABELLED}.csv; "
                         "were microbiology_ekp.csv and spell_microbiology.csv exported from the same build?")
    samples = merged
    record_step(cfg, "samples collected in infection spells", samples)

    samples = exclude_screening_samples(samples, cfg)
    record_step(cfg, "non-screening samples", samples)

    samples = add_surgery_length(attribute_surgery(samples, clean_surgeries(surgeries, cfg), cfg), cfg)
    flags = eligibility_flags(samples, cfg)
    keep = pd.Series(True, index=samples.index)
    labels = {"postoperative": "postoperative samples",
              "at_least_min_hours_after_admission": f"collected >= {cfg['sample_eligibility']['min_hours_after_admission']} h after admission",
              "within_max_days_after_surgery": f"collected <= {cfg['sample_eligibility']['max_days_after_surgery']} d after surgery"}
    for rule in flags.columns:
        keep &= flags[rule]
        record_step(cfg, labels[rule], samples[keep])
    samples = samples[keep].copy()

    samples = group_specimen_site(map_specialty(samples, cfg), cfg)
    if samples.empty:
        raise ValueError("run_cohort_samples: no eligible samples left")
    write_checkpoint(samples, cfg, ELIGIBLE)
    return samples


def run_episodes(cfg: dict) -> pd.DataFrame:
    """Assign infection episodes and collapse to one row per episode.

    Grain: one row per infection episode (``infection_id``).
    """
    cols, status_col = cfg["columns"], cfg["columns"]["outcome"]
    dates = [cols["collect"], cols["admission"], cols["discharge"], cols["surgery_start"], cols["surgery_stop"], cols["surgery_anchor"]]
    samples = _status_as_int(read_checkpoint(cfg, ELIGIBLE, date_cols=dates), cfg)

    samples = assign_infection_episodes(samples, cfg)
    record_step(cfg, "infection episodes", samples, episode_col="infection_id")
    episodes = aggregate_episodes(samples, cfg)
    episodes = exclude_episodes_without_status(episodes, cfg)
    record_step(cfg, "episodes with an ESBL status", episodes, episode_col="infection_id")
    episodes = exclude_other_site_episodes(episodes, cfg)
    record_step(cfg, "episodes with a study specimen site", episodes, episode_col="infection_id")

    assert episodes["infection_id"].is_unique, "run_episodes: infection_id is not unique"
    assert episodes[status_col].isin([0, 1]).all(), "run_episodes: outcome must be 0 or 1 in every episode"
    if episodes.empty:
        raise ValueError("run_episodes: no episodes left")
    write_checkpoint(episodes, cfg, EPISODES)
    return episodes


def run_exposures(cfg: dict) -> pd.DataFrame:
    """Add demographics and the prior-exposure flags to each episode.

    Grain: one row per infection episode (unchanged from ``run_episodes``).
    """
    cols = cfg["columns"]
    episodes = _status_as_int(read_checkpoint(cfg, EPISODES, date_cols=EPISODE_DATES), cfg)

    demographics = clean_demographics(
        read_raw(cfg, "demographics", date_cols=["death_date"], usecols=list(cols["demographics"])), cfg)
    episodes = attach_demographics(episodes, demographics, cfg)
    episodes = set_sparse_ethnicity_missing(episodes, cfg)

    ec = cfg["exposures"]["inpatient_columns"]
    prior = read_raw(cfg, "inpatient_exposure", date_cols=list(ec.values()))
    episodes = flag_prior_inpatient(episodes, prior, cfg)

    all_samples = _status_as_int(read_checkpoint(cfg, LABELLED, date_cols=[cols["collect"]]), cfg)
    episodes = flag_prior_esbl(episodes, all_samples, cfg)

    assert episodes["infection_id"].is_unique, "run_exposures: infection_id is not unique"
    write_checkpoint(episodes, cfg, EXPOSURES)
    return episodes


def run_comorbidity(cfg: dict) -> pd.DataFrame:
    """Add the 17 Charlson flags, ``cci_score`` and ``cci_group`` (via comorbidicare) to each episode.

    Grain: one row per infection episode (unchanged from ``run_exposures``).
    """
    dates, text = cfg["cci"]["date_columns"], cfg["cci"]["text_columns"]
    episodes = _status_as_int(read_checkpoint(cfg, EXPOSURES, date_cols=EPISODE_DATES), cfg)
    diagnoses = read_raw(cfg, "diagnoses", date_cols=dates["diagnoses"], text_cols=text["diagnoses"])
    prescriptions = read_raw(cfg, "prescriptions", date_cols=dates["prescriptions"])
    problems = read_raw(cfg, "problems", date_cols=dates["problems"], usecols=list(cfg["columns"]["problems"]),
                        text_cols=text["problems"])

    episodes = add_comorbidities(episodes, diagnoses, prescriptions, problems, cfg)
    assert episodes["infection_id"].is_unique, "run_comorbidity: infection_id is not unique"
    write_checkpoint(episodes, cfg, COMORBIDITY)
    return episodes


# Order matters: each step reads the checkpoint of an earlier one.
STEPS = {"label": run_label, "cohort": run_cohort_samples, "episodes": run_episodes, "exposures": run_exposures, "comorbidity": run_comorbidity}
