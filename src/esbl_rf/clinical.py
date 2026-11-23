"""Descriptive clinical variables: temperature and CRP near the index sample, in-hospital mortality.

None of these enters the models (temperature and CRP are signs of the infection,
not risk factors for it; decided 2026-11-23). Analysis plan section 7.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def add_nearest_measurement(episodes: pd.DataFrame, obs: pd.DataFrame, name: str, cfg: dict) -> pd.DataFrame:
    """Add the measurement closest in time to the episode's first culture.

    Grain: one row per infection episode (unchanged); ``obs`` has one row per
    measurement (``temperature.csv`` or ``crp.csv``, already limited to the spell
    in SQL). A measurement counts if it is in the same spell, at or after the
    episode's surgery start, and within ``clinical.window_hours`` of the first
    culture (before or after). Ties go to the earlier measurement. Values that
    are not numeric or fall outside ``clinical.<name>.plausible`` are dropped
    and counted. No measurement = missing (not imputed).

    Parameters
    ----------
    name : str
        ``"temperature"`` or ``"crp"``: the key under ``config.yaml: clinical``
        and the output column name.
    """
    cols, spec = cfg["columns"], cfg["clinical"][name]
    subject, spell = cols["subject"], cols["spell"]
    value, time = spec["value_col"], spec["time_col"]
    for what, df, needed in (("episodes", episodes, ["infection_id", subject, spell, "first_culture_dt", "surgery_start_dt"]),
                             (name, obs, [subject, spell, value, time])):
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise ValueError(f"add_nearest_measurement[{name}]: {what} is missing column(s) {missing}")
    if name in episodes.columns:
        raise ValueError(f"add_nearest_measurement[{name}]: episodes already has '{name}'")

    m = obs[[subject, spell, time]].copy()
    m["_value"] = pd.to_numeric(obs[value], errors="coerce")
    low, high = spec["plausible"]
    n_text = int((m["_value"].isna() & obs[value].notna()).sum())
    implausible = m["_value"].notna() & ~m["_value"].between(low, high)
    log.info("add_nearest_measurement[%s]: %d of %d value(s) not numeric, %d outside [%s, %s]; dropped",
             name, n_text, len(m), int(implausible.sum()), low, high)
    m = m[m["_value"].notna() & ~implausible & m[time].notna()]

    window = pd.Timedelta(hours=cfg["clinical"]["window_hours"])
    pairs = episodes[["infection_id", subject, spell, "first_culture_dt", "surgery_start_dt"]].merge(
        m, on=[subject, spell], how="inner")
    gap = (pairs[time] - pairs["first_culture_dt"]).abs()
    pairs = pairs[(gap <= window) & (pairs[time] >= pairs["surgery_start_dt"])].assign(_gap=gap)
    nearest = (pairs.sort_values(["infection_id", "_gap", time], kind="stable")
               .drop_duplicates("infection_id")[["infection_id", "_value"]].rename(columns={"_value": name}))

    out = episodes.merge(nearest, on="infection_id", how="left", validate="one_to_one")
    log.info("add_nearest_measurement[%s]: %d of %d episodes have a value within %s h",
             name, int(out[name].notna().sum()), len(out), cfg["clinical"]["window_hours"])
    return out


def flag_in_hospital_death(episodes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``in_hosp_mortality`` from the spell's discharge method.

    Grain: one row per infection episode (unchanged). 1 if ``discharge_method``
    is in ``mortality.died_codes`` (NHS Data Dictionary: 4 = patient died), 0
    for other known codes, missing if the code is missing or in
    ``mortality.unknown_codes``. ``death_date`` is not used: it is anonymised to
    month/year in iCARE (data dictionary), so it cannot place a death inside a
    spell (decided 2026-11-23: in-hospital mortality only).
    """
    mc = cfg["mortality"]
    col = mc["discharge_method_col"]
    if col not in episodes.columns:
        raise ValueError(f"flag_in_hospital_death: episodes has no '{col}'; check config.yaml: episode_table")
    if "in_hosp_mortality" in episodes.columns:
        raise ValueError("flag_in_hospital_death: episodes already has 'in_hosp_mortality'")
    code = pd.to_numeric(episodes[col], errors="coerce")
    n_text = int((code.isna() & episodes[col].notna()).sum())
    if n_text:
        log.warning("flag_in_hospital_death: %d discharge method value(s) are not numeric codes; set to missing", n_text)
    out = episodes.copy()
    out["in_hosp_mortality"] = code.isin(mc["died_codes"]).astype("Int64").where(
        code.notna() & ~code.isin(mc["unknown_codes"]))
    log.info("flag_in_hospital_death: discharge method codes %s; %d death(s), %d missing",
             code.value_counts(dropna=False).to_dict(), int(out["in_hosp_mortality"].sum()),
             int(out["in_hosp_mortality"].isna().sum()))
    return out
