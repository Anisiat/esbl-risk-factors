"""Cohort derivation log: patients, samples and episodes left after each step.

The first boxes of the flow diagram (adult elective admissions, emergency
exclusion, admissions with an eligible culture) are counted in SQL
(``qa_flowchart_sql.csv``). This log starts from the data Python receives;
``build_flowchart`` joins the two into the full flow diagram table.

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
_COLUMNS = ["step", "n_admissions", "n_patients", "n_samples", "n_episodes", "n_esbl_pos"]


def _read_log(cfg: dict) -> pd.DataFrame:
    """The log so far (grain: one row per step); empty if no step has been recorded."""
    if not checkpoint_path(cfg, STEP).is_file():
        return pd.DataFrame(columns=_COLUMNS).astype({c: "Int64" for c in _COLUMNS[1:]})
    log_df = read_checkpoint(cfg, STEP)[_COLUMNS]
    return log_df.astype({c: "Int64" for c in _COLUMNS[1:]})


def reset_derivation(cfg: dict) -> None:
    """Empty the log. Call before a full run so rows from an older run do not remain."""
    write_checkpoint(_read_log(cfg).iloc[0:0], cfg, STEP)


def record_step(cfg: dict, step: str, df: pd.DataFrame, episode_col: str | None = None,
                esbl_col: str | None = None) -> None:
    """Record how many admissions, patients, samples and episodes are left after ``step``.

    Recording a step that already exists replaces its row in place, so re-running
    a step does not duplicate it.

    Parameters
    ----------
    cfg : dict
        Configuration from ``load_config``; gives the subject column and the sample key.
    step : str
        Name shown in the flow diagram, e.g. ``"collected >= 48 h after admission"``.
    df : pd.DataFrame
        Data after the step. Needs the subject column. ``n_admissions`` is
        counted when the spell column is present, ``n_samples`` when the sample
        key columns are present (samples, not episode rows), and ``n_episodes``
        when ``episode_col`` is given.
    episode_col : str, optional
        Infection episode id column.
    esbl_col : str, optional
        Episode-level outcome (1 = ESBL-positive); counts ``n_esbl_pos``.
        Needs ``episode_col`` and one row per episode.

    Raises
    ------
    ValueError
        If a needed column is missing, ``esbl_col`` is given for a table that is
        not one row per episode, or the patient count is higher than at the
        previous step (an exclusion cannot add patients).
    """
    subject, spell = cfg["columns"]["subject"], cfg["columns"]["spell"]
    key = list(cfg["columns"]["sample_key"])
    if esbl_col and not episode_col:
        raise ValueError(f"record_step['{step}']: esbl_col needs episode_col")
    needed = [subject] + [c for c in (episode_col, esbl_col) if c]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"record_step['{step}']: missing column(s) {missing}")
    if esbl_col and not df[episode_col].is_unique:
        raise ValueError(f"record_step['{step}']: esbl_col needs one row per episode; "
                         f"{int(df[episode_col].duplicated().sum())} duplicated '{episode_col}' value(s)")

    entry = {
        "step": step,
        "n_admissions": int(df[[subject, spell]].drop_duplicates().shape[0]) if spell in df.columns else pd.NA,
        "n_patients": int(df[subject].nunique()),
        "n_samples": int(df[key].drop_duplicates().shape[0]) if set(key) <= set(df.columns) else pd.NA,
        "n_episodes": int(df[episode_col].nunique()) if episode_col else pd.NA,
        "n_esbl_pos": int((df[esbl_col] == 1).sum()) if esbl_col else pd.NA,
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
    log.info("cohort derivation | %-45s admissions=%s patients=%s samples=%s episodes=%s esbl_pos=%s",
             step, entry["n_admissions"], entry["n_patients"], entry["n_samples"], entry["n_episodes"],
             entry["n_esbl_pos"])


def derivation_table(cfg: dict) -> pd.DataFrame:
    """The log with the number removed at each step (grain: one row per step)."""
    out = _read_log(cfg)
    for col in ("admissions", "patients", "samples", "episodes"):
        out[f"{col}_excluded"] = out[f"n_{col}"].shift() - out[f"n_{col}"]
    return out


_SQL_COLUMNS = ["step", "description", "n_admissions", "n_patients", "n_surgery_events", "n_cultures"]
_COUNTS = ["n_admissions", "n_patients", "n_surgery_events", "n_samples", "n_episodes", "n_esbl_pos"]


def build_flowchart(sql_flow: pd.DataFrame, derivation: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Join the SQL flowchart steps and the Python derivation log into one table.

    Grain: one row per flowchart box, in order. SQL rows come first
    (``qa_flowchart_sql.csv``: full elective cohort, emergency exclusion,
    admissions with an eligible culture); Python rows follow (``derivation_table``).
    Informational SQL rows (``config.yaml: flowchart.sql_informational_steps``,
    e.g. an "of which" subset) are kept but sit outside the exclusion chain:
    their ``*_excluded`` counts are empty and they are skipped by the checks.
    Counts are not suppressed here; the export step does that.

    Parameters
    ----------
    sql_flow : pd.DataFrame
        ``qa_flowchart_sql.csv`` (lower-case headers), one row per SQL step.
    derivation : pd.DataFrame
        ``derivation_table(cfg)``, one row per Python step.
    cfg : dict
        Configuration from ``load_config``.

    Returns
    -------
    pd.DataFrame
        Columns ``order, source, sql_step, description, informational``, the
        counts in ``_COUNTS`` and ``<count>_excluded`` for admissions,
        patients, samples and episodes.

    Raises
    ------
    ValueError
        If a column is missing, the SQL link step is absent, the first Python
        step does not have the same admissions and patients as the SQL link step
        (exports from different builds), or patients increase along the chain.
    """
    step = "build_flowchart"
    fc = cfg["flowchart"]
    missing = [c for c in _SQL_COLUMNS if c not in sql_flow.columns]
    if missing:
        raise ValueError(f"{step}: qa_flowchart_sql.csv is missing column(s) {missing}")
    if derivation.empty:
        raise ValueError(f"{step}: the cohort derivation log is empty; run the cohort and episode steps first")

    sql = sql_flow[_SQL_COLUMNS].rename(columns={"step": "sql_step", "n_cultures": "n_samples"})
    sql = sql.sort_values("sql_step", kind="stable").assign(source="sql")
    sql["informational"] = sql["sql_step"].isin(fc["sql_informational_steps"])
    link = sql.loc[sql["sql_step"] == fc["sql_link_step"]]
    if len(link) != 1:
        raise ValueError(f"{step}: expected one SQL row with step {fc['sql_link_step']} "
                         f"(config.yaml: flowchart.sql_link_step), found {len(link)}")

    py = derivation.rename(columns={"step": "description"}).assign(source="python", sql_step=pd.NA, informational=False)
    first = py.iloc[0]
    for col in ("n_admissions", "n_patients"):
        if int(first[col]) != int(link.iloc[0][col]):
            raise ValueError(f"{step}: {col} is {int(first[col])} at Python step '{first['description']}' but "
                             f"{int(link.iloc[0][col])} at SQL step {fc['sql_link_step']}; were the CSVs exported "
                             "from the same SQL build?")

    out = pd.concat([sql, py], ignore_index=True)
    out = out[["source", "sql_step", "description", "informational", *_COUNTS]]
    out[_COUNTS] = out[_COUNTS].apply(pd.to_numeric, errors="raise").astype("Int64")
    out.insert(0, "order", range(1, len(out) + 1))

    chain = out.loc[~out["informational"]]
    rising = chain["n_patients"].diff() > 0
    if rising.any():
        bad = chain.loc[rising, "description"].tolist()
        raise ValueError(f"{step}: patient count rises at {bad}; an exclusion cannot add patients")
    for col in ("admissions", "patients", "samples", "episodes"):
        out[f"{col}_excluded"] = (chain[f"n_{col}"].shift() - chain[f"n_{col}"]).reindex(out.index).astype("Int64")
    # SQL counts rows per (spell, lab test, organism), Python distinct (subject, lab test,
    # organism): the first Python box is the same cohort at another grain, not an exclusion.
    out.loc[out["source"].eq("python").idxmax(), "samples_excluded"] = pd.NA
    log.info("%s: %d boxes (%d SQL, %d Python)", step, len(out), len(sql), len(py))
    return out
