"""Load and validate ``config.yaml``."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

_REQUIRED: dict[str, tuple[str, ...]] = {
    "study": ("study_start", "study_end", "min_age", "elective_admission_methods", "organisms"),
    "sample_eligibility": ("min_hours_after_admission", "max_days_after_surgery", "screening"),
    "surgery": ("meta_procedure_labels",),
    "sites": ("other_label", "groups", "hierarchy"),
    "episode_table": ("spell_columns", "index_sample_columns"),
    "episodes": ("gap_days",),
    "demographics": ("ethnicity_groups", "ethnicity_min_count", "ethnicity_sparse_into", "gender_groups"),
    "exposures": ("lookback_days", "prior_esbl_lookback_days", "inpatient_columns"),
    "outcome": ("resistant_drugs", "susceptible_alone_drugs", "susceptible_pairs", "marker_prefix",
                "marker_positive", "marker_negative", "esbl_statement_column"),
    "paths": ("raw_dir", "lookup_dir", "interim_dir", "export_dir"),
    "raw_csv": (),
    "lookup_csv": (),
    "tfc_groups": (),
    "columns": ("subject", "spell", "admission", "discharge", "collect", "surgery_start",
                "surgery_stop", "surgery_anchor", "procedure", "culture_type", "site_group", "tfc_desc", "tfc_group", "organism", "culture_code", "site", "surgery_columns",
                "sample_key", "outcome"),
    "table1": ("blocks", "categorical", "binary", "supplementary_conditions"),
    "labels": ("groups", "variables", "levels"),
    "antibiotics": ("medication_col", "route_col", "time_col", "non_antibiotic_suffixes", "exclude", "keep",
                    "therapeutic_agents"),
    "prophylaxis": ("iv_routes", "combine_minutes", "agent_classes", "group_labels"),
    "clinical": ("window_hours", "temperature", "crp"),
    "mortality": ("discharge_method_col", "died_codes", "unknown_codes"),
    "flowchart": ("sql_link_step", "sql_informational_steps"),
    "cci": ("cutoff", "date_columns", "text_columns", "group_edges", "group_labels"),
    "logistic_regression": ("cluster_col", "order_col", "predictors", "reference_levels", "screen_p",
                            "clinically_important", "sparse", "vif_flag", "subgroups", "min_subgroup_epv",
                            "stable_subgroup_epv"),
}
_PREDICTOR_TYPES = ("continuous", "binary", "categorical")


def _check_structure(cfg: dict) -> list[str]:
    """Missing sections and keys. Later checks need these, so they run first."""
    problems = []
    for section, keys in _REQUIRED.items():
        if section not in cfg:
            problems.append(f"missing section '{section}'")
            continue
        problems += [f"missing key '{section}.{k}'" for k in keys if k not in cfg[section]]
    if "suppression_threshold" not in cfg:
        problems.append("missing key 'suppression_threshold'")
    return problems


def _check_values(cfg: dict) -> list[str]:
    """Values that are present but inconsistent."""
    problems = []

    start, end = pd.Timestamp(cfg["study"]["study_start"]), pd.Timestamp(cfg["study"]["study_end"])
    if start >= end:
        problems.append(f"study_start {start.date()} is not before study_end {end.date()}")

    for key in ("min_hours_after_admission", "max_days_after_surgery"):
        if cfg["sample_eligibility"][key] < 0:
            problems.append(f"sample_eligibility.{key} is negative")
    if cfg["episodes"]["gap_days"] <= 0:
        problems.append("episodes.gap_days must be positive")
    if cfg["exposures"]["lookback_days"] <= 0:
        problems.append("exposures.lookback_days must be positive")
    if cfg["demographics"]["ethnicity_min_count"] < 0:
        problems.append("demographics.ethnicity_min_count is negative")
    if cfg["demographics"]["ethnicity_sparse_into"] not in set(cfg["demographics"]["ethnicity_groups"].values()):
        problems.append(f"demographics.ethnicity_sparse_into '{cfg['demographics']['ethnicity_sparse_into']}' "
                        "is not an ethnicity group")
    ref = cfg["logistic_regression"]["reference_levels"].get("prophylaxis_group")
    if ref is not None and ref not in cfg["prophylaxis"]["group_labels"].values():
        problems.append(f"logistic_regression.reference_levels.prophylaxis_group '{ref}' is not in "
                        "prophylaxis.group_labels")
    if cfg["suppression_threshold"] < 1:
        problems.append("suppression_threshold must be at least 1")

    cci = cfg["cci"]
    if cci["cutoff"] not in ("admission", "discharge"):
        problems.append(f"cci.cutoff is '{cci['cutoff']}', expected 'admission' or 'discharge'")
    if len(cci["group_labels"]) != len(cci["group_edges"]):
        problems.append(
            f"cci.group_labels ({len(cci['group_labels'])}) must match cci.group_edges "
            f"({len(cci['group_edges'])}): each edge starts one group"
        )
    if list(cci["group_edges"]) != sorted(set(cci["group_edges"])):
        problems.append("cci.group_edges must be strictly increasing")

    sites = cfg["sites"]
    labels = [g.get("label") for g in sites["groups"]]
    for g in sites["groups"]:
        try:
            re.compile(g.get("pattern", ""))
        except re.error as err:
            problems.append(f"sites.groups pattern for '{g.get('label')}' is not a valid regex: {err}")
    absent = sorted((set(labels) | {sites["other_label"]}) - set(sites["hierarchy"]))
    if absent:
        problems.append(f"sites.hierarchy is missing site group(s) {absent}")
    unknown = sorted(set(sites["hierarchy"]) - set(labels) - {sites["other_label"]})
    if unknown:
        problems.append(f"sites.hierarchy has label(s) with no group: {unknown}")

    table1 = cfg["table1"]
    listed = [c for block in table1["blocks"].values() for c in block]
    dupes = sorted({c for c in listed if listed.count(c) > 1})
    if dupes:
        problems.append(f"table1.blocks lists column(s) more than once: {dupes}")
    for key in ("categorical", "binary"):
        extra = sorted(set(table1[key]) - set(listed))
        if extra:
            problems.append(f"table1.{key} has columns not in table1.blocks: {extra}")
    overlap = sorted(set(table1["categorical"]) & set(table1["binary"]))
    if overlap:
        problems.append(f"table1.categorical and table1.binary overlap: {overlap}")
    if set(cfg["labels"]["groups"]) != {"overall", "esbl_pos", "non_esbl"}:
        problems.append("labels.groups must have exactly the keys overall, esbl_pos, non_esbl")

    lr = cfg["logistic_regression"]
    predictors = lr["predictors"]
    missing_types = [t for t in _PREDICTOR_TYPES if t not in predictors]
    if missing_types:
        problems.append(f"logistic_regression.predictors missing types: {missing_types}")
    else:
        flat = [p for t in _PREDICTOR_TYPES for p in predictors[t]]
        dupes = sorted({p for p in flat if flat.count(p) > 1})
        if dupes:
            problems.append(f"logistic_regression.predictors listed more than once: {dupes}")
        extra = sorted(set(lr["reference_levels"]) - set(predictors["categorical"]))
        if extra:
            problems.append(f"logistic_regression.reference_levels for non-categorical predictors: {extra}")
        unset = sorted(set(predictors["categorical"]) - set(lr["reference_levels"]))
        if unset:
            problems.append(f"logistic_regression.reference_levels missing for categorical predictors: {unset}")
        for key, values, allowed in (("clinically_important", lr["clinically_important"], flat),
                                     ("subgroups", lr["subgroups"], predictors["categorical"]),
                                     ("sparse.merge_into", lr["sparse"].get("merge_into", {}), predictors["categorical"])):
            extra = sorted(set(values) - set(allowed))
            if extra:
                problems.append(f"logistic_regression.{key} has names that are not (categorical) predictors: {extra}")
    if not 0 < lr["screen_p"] < 1:
        problems.append("logistic_regression.screen_p must be between 0 and 1")
    if not 0 < lr["min_subgroup_epv"] <= lr["stable_subgroup_epv"]:
        problems.append("logistic_regression: need 0 < min_subgroup_epv <= stable_subgroup_epv")
    if "min_events" not in lr["sparse"] or "merge_into" not in lr["sparse"]:
        problems.append("logistic_regression.sparse needs min_events and merge_into")
    return problems


def load_config(path: Path | str) -> dict:
    """Read ``config.yaml``, validate it and make paths absolute.

    Parameters
    ----------
    path : Path or str
        Location of the YAML file. Relative paths inside it are resolved against
        this file's directory, so the working directory never matters.

    Returns
    -------
    dict
        The configuration, with ``paths.*_dir`` as absolute ``Path`` objects
        and every entry of ``raw_csv`` and ``lookup_csv`` replaced by the
        absolute ``Path`` of that file. Files are not checked for existence
        here (they are checked when read). ``config_path`` records the YAML
        location.

    Raises
    ------
    ValueError
        Listing every missing key and inconsistent value, not just the first.
    """
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    problems = _check_structure(cfg)
    if not problems:
        problems = _check_values(cfg)
    if problems:
        raise ValueError(f"load_config: {path} is invalid:\n  - " + "\n  - ".join(problems))

    base = path.parent
    paths = cfg["paths"]
    for key in ("raw_dir", "lookup_dir", "interim_dir", "export_dir"):
        paths[key] = (base / paths[key]).resolve()
    cfg["raw_csv"] = {name: paths["raw_dir"] / fname for name, fname in cfg["raw_csv"].items()}
    cfg["lookup_csv"] = {name: paths["lookup_dir"] / fname for name, fname in cfg["lookup_csv"].items()}
    cfg["config_path"] = path
    return cfg


def study_window(cfg: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return ``(start, end_exclusive)`` for the study window.

    ``study_end`` is inclusive in the config, so the exclusive end is the
    following midnight (a surgery at 2025-09-30 18:00 is inside the window).
    """
    start = pd.Timestamp(cfg["study"]["study_start"])
    end_exclusive = pd.Timestamp(cfg["study"]["study_end"]) + pd.Timedelta(days=1)
    return start, end_exclusive
