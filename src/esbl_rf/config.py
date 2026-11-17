"""Load and validate ``config.yaml``."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

_REQUIRED: dict[str, tuple[str, ...]] = {
    "study": ("study_start", "study_end", "min_age", "elective_admission_methods", "organisms"),
    "sample_eligibility": ("min_hours_after_admission", "max_days_after_surgery"),
    "episodes": ("gap_days",),
    "outcome": ("third_gen_columns", "cefpodoxime_column", "marker_column", "marker_positive",
                "marker_negative", "esbl_statement_column", "susceptible_pairs"),
    "paths": ("raw_dir", "lookup_dir", "interim_dir", "export_dir"),
    "raw_csv": (),
    "lookup_csv": (),
    "tfc_groups": (),
    "columns": ("subject", "spell", "admission", "discharge", "collect", "surgery_start",
                "surgery_stop", "surgery_anchor", "organism", "sample_key", "outcome"),
    "table1": ("columns", "categorical", "nonnormal", "supplementary_conditions"),
    "cci": ("cutoff", "group_edges", "group_labels"),
    "logistic_regression": ("cluster_col", "id_columns", "predictors", "reference_levels"),
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

    table1 = cfg["table1"]
    for key in ("categorical", "nonnormal"):
        extra = sorted(set(table1[key]) - set(table1["columns"]))
        if extra:
            problems.append(f"table1.{key} has columns not in table1.columns: {extra}")
    overlap = sorted(set(table1["categorical"]) & set(table1["nonnormal"]))
    if overlap:
        problems.append(f"table1.categorical and table1.nonnormal overlap: {overlap}")

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
