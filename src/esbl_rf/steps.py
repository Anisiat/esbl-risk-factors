"""Pipeline steps: each reads its input, calls the pure functions, logs counts and writes a checkpoint.

A step is plumbing only (no analysis logic): ``run_<step>(cfg) -> DataFrame`` reads
the previous checkpoint (or raw CSVs), calls functions from the other modules,
records counts, asserts its own invariants and writes ``data/interim/<step>.csv``.
``scripts/run_all.py`` runs the steps in order.
"""
from __future__ import annotations

import logging

import pandas as pd

from esbl_rf.antibiotics import add_prophylaxis, choose_prophylaxis, filter_antibiotics, flag_past_abx, log_value_counts
from esbl_rf.clinical import add_nearest_measurement, flag_in_hospital_death
from esbl_rf.cohort_derivation import build_flowchart, derivation_table, record_step
from esbl_rf.comorbidity import add_comorbidities
from esbl_rf.demographics import attach_demographics, clean_demographics, merge_sparse_ethnicity
from esbl_rf.eligibility import eligibility_flags, exclude_screening_samples
from esbl_rf.export import (definitions_table, diagnostics_display, flowchart_display, forest_plot, subgroup_display, table1_display,
                             table2_display, write_tables)
from esbl_rf.episodes import (aggregate_episodes, assign_infection_episodes, exclude_episodes_without_status,
                              exclude_other_site_episodes)
from esbl_rf.exposures import flag_prior_esbl, flag_prior_inpatient
from esbl_rf.io import read_checkpoint, read_lookup, read_raw, write_checkpoint
from esbl_rf.models import (complete_case_loss, first_episode_per_patient, multivariable, prepare_model_data,
                            select_predictors, subgroups, univariable)
from esbl_rf.outcome import build_ast_rules, label_esbl_status
from esbl_rf.sites import group_specimen_site
from esbl_rf.surgery import add_surgery_length, attribute_surgery, clean_surgeries, map_specialty
from esbl_rf.tables import log_sparse_levels, summarise_table, supplement_blocks, table1_blocks

log = logging.getLogger(__name__)

LABELLED, ELIGIBLE, EPISODES, EXPOSURES = "microbiology_labelled", "eligible_samples", "episodes", "episode_exposures"
COMORBIDITY, CLINICAL, FLOWCHART = "episode_comorbidity", "episode_clinical", "flowchart"
TABLE1, TABLE1_SUPP = "table1", "table1_supplement"
MODEL_OUTPUTS = ("model_univariable", "model_selection", "model_terms", "model_diagnostics", "model_vif",
                 "model_complete_case", "model_subgroups", "model_merged_levels")
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
    imd = cfg["episode_table"]["spell_columns"]["imd_decile"]
    is_none = samples[imd].astype("string").str.strip().str.lower().isin(["none"])
    log.info("run_cohort_samples: %d sample row(s) have IMD 'none' (set to missing)", int(is_none.sum()))
    samples[imd] = pd.to_numeric(samples[imd].mask(is_none), errors="raise")
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
    record_step(cfg, "episodes with an ESBL status", episodes, episode_col="infection_id", esbl_col=status_col)
    episodes = exclude_other_site_episodes(episodes, cfg)
    record_step(cfg, "episodes with a study specimen site", episodes, episode_col="infection_id", esbl_col=status_col)

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
    episodes = merge_sparse_ethnicity(episodes, cfg)

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


def run_clinical(cfg: dict) -> pd.DataFrame:
    """Add past antibiotics, prophylaxis, temperature, CRP and in-hospital mortality to each episode.

    Grain: one row per infection episode (unchanged from ``run_comorbidity``).
    """
    cols, ab, clin = cfg["columns"], cfg["antibiotics"], cfg["clinical"]
    event = "surgery_event_id"
    episodes = _status_as_int(read_checkpoint(cfg, COMORBIDITY, date_cols=EPISODE_DATES), cfg)
    n_no_surgery = int(episodes[event].isna().sum())
    if n_no_surgery:
        raise ValueError(f"run_clinical: {n_no_surgery} episode(s) have no attributed surgery ('{event}')")
    episodes[event] = episodes[event].astype("string")

    admin = read_raw(cfg, "abx_administrations", date_cols=[ab["time_col"]])
    log_value_counts(admin, [ab["route_col"], "administration_status"], "abx_administrations")
    episodes = flag_past_abx(episodes, filter_antibiotics(admin, cfg), cfg)

    surgery_abx = read_raw(cfg, "surgery_abx", date_cols=[ab["time_col"], cols["surgery_start"], cols["surgery_stop"]],
                           text_cols=[event])
    log_value_counts(surgery_abx, [ab["route_col"], "administration_status"], "surgery_abx")
    episodes = add_prophylaxis(episodes, choose_prophylaxis(filter_antibiotics(surgery_abx, cfg), cfg), cfg)

    for name in ("temperature", "crp"):
        obs = read_raw(cfg, name, date_cols=[clin[name]["time_col"]])
        episodes = add_nearest_measurement(episodes, obs, name, cfg)
    episodes = flag_in_hospital_death(episodes, cfg)

    assert episodes["infection_id"].is_unique, "run_clinical: infection_id is not unique"
    write_checkpoint(episodes, cfg, CLINICAL)
    return episodes


def run_flowchart(cfg: dict) -> pd.DataFrame:
    """Full cohort flow diagram: SQL steps (``qa_flowchart_sql.csv``) then the Python derivation log.

    Grain: one row per flowchart box. Unsuppressed; the export step suppresses it.
    """
    sql_flow = read_raw(cfg, "qa_flowchart_sql")
    flowchart = build_flowchart(sql_flow, derivation_table(cfg), cfg)
    write_checkpoint(flowchart, cfg, FLOWCHART)
    return flowchart


def run_table1(cfg: dict) -> pd.DataFrame:
    """Table 1 and its supplement (all 17 Charlson conditions) by ESBL status, as unsuppressed numbers.

    Grain: one row per table row (see ``esbl_rf.tables.summarise_table``). The
    export step suppresses and formats them.
    """
    episodes = _status_as_int(read_checkpoint(cfg, CLINICAL), cfg)
    table1 = summarise_table(episodes, table1_blocks(cfg), cfg)
    log_sparse_levels(table1, cfg)
    supplement = summarise_table(episodes, supplement_blocks(cfg), cfg)
    for name, table in ((TABLE1, table1), (TABLE1_SUPP, supplement)):
        assert table.loc[0, "overall_n"] == len(episodes), f"run_table1: {name} N does not match the episodes"
        write_checkpoint(table, cfg, name)
    return table1


def run_models(cfg: dict) -> pd.DataFrame:
    """Univariable screen, primary clustered model, first-episode sensitivity model and subgroups.

    Grain of the input: one row per infection episode. Writes one checkpoint per
    table in ``MODEL_OUTPUTS`` (unsuppressed; the export step formats and
    suppresses them) and returns the multivariable terms.
    """
    lr = cfg["logistic_regression"]
    episodes = _status_as_int(read_checkpoint(cfg, CLINICAL), cfg)
    data, merged = prepare_model_data(episodes, cfg)
    uni = univariable(data, cfg)
    selection = select_predictors(uni, cfg)
    chosen = selection.loc[selection["selected"], "variable"].tolist()

    primary, primary_diag, primary_vif = multivariable(data, chosen, cfg, "primary", clustered=True)
    first = first_episode_per_patient(data, cfg)
    assert first[lr["cluster_col"]].is_unique, "run_models: first-episode data repeats a patient"
    sens, sens_diag, sens_vif = multivariable(first, chosen, cfg, "first_episode", clustered=False)
    assert primary_diag["n_used"] > 0 and sens_diag["n_used"] > 0, "run_models: a model used no episodes"
    merged_text = "; ".join(f"{r.variable}: {r.level} -> {r.merged_into}" for r in merged.itertuples()) or None
    diagnostics = pd.DataFrame([primary_diag, sens_diag]).assign(merged_levels=merged_text)

    outputs = {
        "model_univariable": uni,
        "model_selection": selection,
        "model_terms": pd.concat([primary, sens], ignore_index=True),
        "model_diagnostics": diagnostics,
        "model_vif": pd.concat([primary_vif, sens_vif], ignore_index=True),
        "model_complete_case": complete_case_loss(data, chosen, cfg),
        "model_subgroups": subgroups(data, chosen, cfg),
        "model_merged_levels": merged,
    }
    assert set(outputs) == set(MODEL_OUTPUTS)
    for name, table in outputs.items():
        write_checkpoint(table, cfg, name)
    return outputs["model_terms"]


def run_export(cfg: dict) -> pd.DataFrame:
    """Suppress, format and write every aggregate output to ``paths.export_dir`` (the last step).

    Reads aggregate checkpoints only (flowchart, Table 1, model tables), never
    episode-level data. Returns a manifest: one row per file written.
    """
    read = lambda name: read_checkpoint(cfg, name)  # noqa: E731
    models = {name: read(name) for name in MODEL_OUTPUTS}
    tables = {
        "flowchart_counts": flowchart_display(read(FLOWCHART), cfg),
        "table1": table1_display(read(TABLE1), cfg),
        "table1_supplement": table1_display(read(TABLE1_SUPP), cfg),
        "table2": table2_display(models["model_univariable"], models["model_terms"], models["model_selection"],
                                 models["model_diagnostics"], cfg),
        "table_s3_subgroups": subgroup_display(models["model_subgroups"], cfg),
        "table_s_definitions": definitions_table(cfg),
    }
    diag = diagnostics_display(models["model_diagnostics"], models["model_vif"], models["model_selection"],
                               models["model_complete_case"], models["model_merged_levels"], cfg)
    tables.update({name: (df, []) for name, df in diag.items()})
    export_dir = cfg["paths"]["export_dir"]
    written = write_tables(tables, export_dir)
    written += forest_plot(models["model_terms"], cfg, export_dir / "figure_forest")
    manifest = pd.DataFrame({"file": [p.name for p in written]})
    log.info("run_export: wrote %d file(s) to %s: %s", len(written), export_dir, manifest["file"].tolist())
    return manifest


# Order matters: each step reads the checkpoint of an earlier one.
STEPS = {"label": run_label, "cohort": run_cohort_samples, "episodes": run_episodes, "exposures": run_exposures,
         "comorbidity": run_comorbidity, "clinical": run_clinical, "flowchart": run_flowchart,
         "table1": run_table1, "models": run_models,
         "export": run_export}
