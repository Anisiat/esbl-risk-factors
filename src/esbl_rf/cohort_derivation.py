"""Cohort derivation log: patients, samples and episodes left after each step.

The first boxes of the flow diagram (adult elective admissions, emergency
exclusion, admissions with an eligible culture) are counted in SQL
(``qa_flowchart_sql.csv``). This log starts from the data Python receives.

The log is one CSV, ``data/interim/cohort_derivation.csv``, so steps run
separately (``run_all.py --only``) all add to the same file. Counts are not
suppressed here; the export step applies small-number suppression.
"""
from __future__ import annotations

import logging

import pandas as pd

from esbl_rf.io import checkpoint_path, read_checkpoint, write_checkpoint

log = logging.getLogger(__name__)

STEP = "cohort_derivation"
_COLUMNS = ["step", "n_patients", "n_samples", "n_episodes"]


def _read_log(cfg: dict) -> pd.DataFrame:
    """The log so far (grain: one row per step); empty if no step has been recorded."""
    if not checkpoint_path(cfg, STEP).is_file():
        return pd.DataFrame(columns=_COLUMNS).astype({c: "Int64" for c in _COLUMNS[1:]})
    log_df = read_checkpoint(cfg, STEP)[_COLUMNS]
    return log_df.astype({c: "Int64" for c in _COLUMNS[1:]})


def reset_derivation(cfg: dict) -> None:
    """Empty the log. Call before a full run so rows from an older run do not remain."""
    write_checkpoint(_read_log(cfg).iloc[0:0], cfg, STEP)


def record_step(cfg: dict, step: str, df: pd.DataFrame, episode_col: str | None = None) -> None:
    """Record how many patients, samples and episodes are left after ``step``.

    Recording a step that already exists replaces its row in place, so re-running
    a step does not duplicate it.

    Parameters
    ----------
    cfg : dict
        Configuration from ``load_config``; gives the subject column and the sample key.
    step : str
        Name shown in the flow diagram, e.g. ``"collected >= 48 h after admission"``.
    df : pd.DataFrame
        Data after the step. Needs the subject column. ``n_samples`` is counted
        when the sample key columns are present (samples, not episode rows),
        and ``n_episodes`` when ``episode_col`` is given.
    episode_col : str, optional
        Infection episode id column.

    Raises
    ------
    ValueError
        If the subject (or episode) column is missing, or the patient count is
        higher than at the previous step (an exclusion cannot add patients).
    """
    subject, key = cfg["columns"]["subject"], list(cfg["columns"]["sample_key"])
    needed = [subject] + ([episode_col] if episode_col else [])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"record_step['{step}']: missing column(s) {missing}")

    entry = {
        "step": step,
        "n_patients": int(df[subject].nunique()),
        "n_samples": int(df[key].drop_duplicates().shape[0]) if set(key) <= set(df.columns) else pd.NA,
        "n_episodes": int(df[episode_col].nunique()) if episode_col else pd.NA,
    }

    current = _read_log(cfg)
    position = current.index[current["step"] == step]
    index_before = (position[0] - 1) if len(position) else len(current) - 1
    if index_before >= 0:
        before = current.iloc[index_before]
        if entry["n_patients"] > before["n_patients"]:
            raise ValueError(
                f"record_step['{step}']: {entry['n_patients']} patients, more than the "
                f"{before['n_patients']} at '{before['step']}'"
            )

    row = pd.DataFrame([entry], columns=_COLUMNS).astype({c: "Int64" for c in _COLUMNS[1:]})
    if len(position):
        current.iloc[position[0]] = row.iloc[0]
        updated = current
    else:
        updated = pd.concat([current, row], ignore_index=True)
    write_checkpoint(updated, cfg, STEP)
    log.info("cohort derivation | %-45s patients=%s samples=%s episodes=%s",
             step, entry["n_patients"], entry["n_samples"], entry["n_episodes"])


def derivation_table(cfg: dict) -> pd.DataFrame:
    """The log with the number removed at each step (grain: one row per step)."""
    out = _read_log(cfg)
    for col in ("patients", "samples", "episodes"):
        out[f"{col}_excluded"] = out[f"n_{col}"].shift() - out[f"n_{col}"]
    return out
