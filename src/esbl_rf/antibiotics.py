"""Antibiotic exposures: prior antibiotics (past_abx) and perioperative prophylaxis.

Ported from the manuscript's ``get_abx_exposure.py``. Both read pharmacy
administrations (doses given, not prescriptions): ``abx_administrations.csv`` for
past antibiotics and ``surgery_abx.csv`` for prophylaxis (analysis_plan section 7).
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def _norm(text: pd.Series) -> pd.Series:
    return text.astype("string").str.strip().str.lower()


def filter_antibiotics(admin: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Keep antibacterial drugs; drop antifungals, antivirals and other non-antibiotics.

    Grain: one row per administration (unchanged, fewer rows). SQL already keeps
    therapeutic class 'anti-infectives'. A drug is dropped if its name ends with a
    ``antibiotics.non_antibiotic_suffixes`` entry or is in ``antibiotics.exclude``,
    unless it is in ``antibiotics.keep`` (e.g. metronidazole ends in 'zole'). Same
    rule as the baseline ``filter_antibiotics``. CHANGE: names are stripped and
    lower-cased first, missing names are dropped (the baseline turned them into
    the string 'nan'), and dropped names are logged with counts. CHANGE:
    administrations whose status is in ``antibiotics.not_given_statuses`` are
    dropped (the baseline kept doses that were not given).
    """
    ab = cfg["antibiotics"]
    med = ab["medication_col"]
    if med not in admin.columns:
        raise ValueError(f"filter_antibiotics: missing column '{med}'")
    out = admin.copy()
    if ab["not_given_statuses"]:
        status = ab["status_col"]
        if status not in out.columns:
            raise ValueError(f"filter_antibiotics: missing column '{status}'")
        not_given = _norm(out[status]).isin([v.lower() for v in ab["not_given_statuses"]]).fillna(False)
        log.info("filter_antibiotics: dropped %d administration(s) not given (%s)", int(not_given.sum()),
                 _norm(out.loc[not_given, status]).value_counts().to_dict())
        out = out[~not_given.to_numpy()]
    out[med] = _norm(out[med])
    named = out[med].notna()
    is_non_abx = out[med].str.endswith(tuple(ab["non_antibiotic_suffixes"])) | out[med].isin(ab["exclude"])
    drop = named & is_non_abx.fillna(False) & ~out[med].isin(ab["keep"])
    if drop.any():
        log.info("filter_antibiotics: dropped %d non-antibiotic administration(s): %s", int(drop.sum()),
                 out.loc[drop, med].value_counts().to_dict())
    n_unnamed = int((~named).sum())
    if n_unnamed:
        log.warning("filter_antibiotics: dropped %d administration(s) with no medication name", n_unnamed)
    return out[named & ~drop].copy()


def log_value_counts(df: pd.DataFrame, columns: list[str], what: str) -> None:
    """Log the categories (not patient data) of route / status columns so the run log shows them."""
    for col in columns:
        if col in df.columns:
            log.info("%s: '%s' values %s", what, col, _norm(df[col]).value_counts(dropna=False).to_dict())


def flag_past_abx(episodes: pd.DataFrame, admin: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ``past_abx``: 1 if an antibiotic was given 1 to ``exposures.lookback_days`` days before admission.

    Grain: one row per infection episode (unchanged). ``admin`` is
    ``abx_administrations.csv`` after ``filter_antibiotics`` (any route), linked
    to the index spell in SQL. Absence of a record = 0. CHANGE vs baseline
    ``get_past_abx``: the window is in calendar days (administration date from
    admission date - 90 to admission date - 1) instead of floored 24-hour
    differences from the admission timestamp.
    """
    cols, ab = cfg["columns"], cfg["antibiotics"]
    subject, spell, time = cols["subject"], cols["spell"], ab["time_col"]
    for name, df, needed in (("episodes", episodes, [subject, spell, "admission_date"]),
                             ("administrations", admin, [subject, spell, time])):
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise ValueError(f"flag_past_abx: {name} is missing column(s) {missing}")
    if "past_abx" in episodes.columns:
        raise ValueError("flag_past_abx: episodes already has 'past_abx'")

    spells = episodes[[subject, spell, "admission_date"]].drop_duplicates([subject, spell])
    linked = admin[[subject, spell, time]].merge(spells, on=[subject, spell], how="inner")
    days_before = (linked["admission_date"].dt.normalize() - linked[time].dt.normalize()).dt.days
    in_window = days_before.between(1, cfg["exposures"]["lookback_days"])
    exposed = linked.loc[in_window, [subject, spell]].drop_duplicates().assign(past_abx=1)

    out = episodes.merge(exposed, on=[subject, spell], how="left", validate="many_to_one")
    out["past_abx"] = out["past_abx"].fillna(0).astype(int)
    log.info("flag_past_abx: %d of %d episodes had antibiotics in the %d days before admission",
             int(out["past_abx"].sum()), len(out), cfg["exposures"]["lookback_days"])
    return out


def agent_class(agents: pd.Series, cfg: dict) -> pd.Series:
    """Class of each single agent name from ``prophylaxis.agent_classes`` (keyword in name), else missing.

    Raises ValueError if an agent matches keywords of more than one class.
    """
    classes = cfg["prophylaxis"]["agent_classes"]
    names = _norm(agents)
    hits = pd.DataFrame({cls: names.apply(lambda n, kws=kws: any(k in n for k in kws) if pd.notna(n) else False)
                         for cls, kws in classes.items()}, index=agents.index)
    multi = hits.sum(axis=1) > 1
    if multi.any():
        raise ValueError(f"agent_class: agent(s) match more than one class in prophylaxis.agent_classes: "
                         f"{sorted(names[multi].unique())}")
    return hits.idxmax(axis=1).where(hits.any(axis=1))


def group_prophylaxis(classes: set[str], cfg: dict) -> str:
    """Group one prophylaxis course from the classes of its agents (rules in the baseline's order).

    ``classes`` holds one entry per agent; an agent with no class is ``None``.
    CHANGE vs baseline ``group_prophylaxis``: rules use each agent's class, not
    substring matches on the joined course string; labels come from config.
    """
    labels = cfg["prophylaxis"]["group_labels"]
    if not classes:
        return labels["none"]
    if "co_amoxiclav" in classes:
        return labels["co_amoxiclav"]
    if "cefuroxime" in classes and "aminoglycoside" in classes:
        return labels["cefuroxime_aminoglycoside"]
    if "cefuroxime" in classes:
        return labels["cefuroxime"]
    if "aminoglycoside" in classes:
        return labels["aminoglycoside"]
    if classes == {"limited_enterobacterales"}:
        return labels["limited_enterobacterales"]
    if "broad_gram_negative" in classes:
        return labels["broad_gram_negative"]
    return labels["other"]


def choose_prophylaxis(surgery_abx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Prophylaxis course per surgery: IV antibacterial doses in the perioperative window.

    Grain: input one row per (surgery, administration) (``surgery_abx.csv``, after
    ``filter_antibiotics``); output one row per ``surgery_event_id`` that had a
    candidate dose. The window (SQL ``in_window``) runs from
    ``prophylaxis_hours_before_start`` before surgery start to
    ``prophylaxis_hours_after_stop`` after surgery stop (00_params.sql).

    As the baseline ``infer_prophylaxis``: the anchor dose is the one closest to
    surgery start, preferring agents not in ``antibiotics.therapeutic_agents``;
    agents given within ``prophylaxis.combine_minutes`` of it form the course
    (non-therapeutic agents only, if there are any). CHANGE: the window ends 1 h
    after surgery stop, not start, and the baseline's second pass (any IV dose
    within 6 h of surgery) is dropped, so the window stays as decided (2026-11-23).

    Returns
    -------
    pd.DataFrame
        ``surgery_event_id``, ``prophylaxis`` (agents joined by " | "),
        ``n_prophylaxis_agents``, ``delta_hours_prophylaxis`` (surgery start minus
        anchor dose time; positive = before surgery) and ``prophylaxis_group``.
    """
    ab, pr = cfg["antibiotics"], cfg["prophylaxis"]
    med, route, time = ab["medication_col"], ab["route_col"], ab["time_col"]
    start, event = cfg["columns"]["surgery_start"], "surgery_event_id"
    needed = [event, start, med, route, time, "in_window"]
    missing = [c for c in needed if c not in surgery_abx.columns]
    if missing:
        raise ValueError(f"choose_prophylaxis: surgery_abx is missing column(s) {missing}")

    df = surgery_abx[needed].copy()
    df[med] = _norm(df[med])
    df = df[(df["in_window"] == 1) & _norm(df[route]).isin(pr["iv_routes"]) & df[time].notna()]
    out_cols = [event, "prophylaxis", "n_prophylaxis_agents", "delta_hours_prophylaxis", "prophylaxis_group"]
    if df.empty:
        log.warning("choose_prophylaxis: no IV antibiotic doses in the perioperative window")
        return pd.DataFrame(columns=out_cols)

    df["delta_hours_prophylaxis"] = (df[start] - df[time]).dt.total_seconds() / 3600
    df["abs_delta"] = df["delta_hours_prophylaxis"].abs()
    df["is_therapeutic"] = df[med].isin(ab["therapeutic_agents"])
    df = df.sort_values([event, "is_therapeutic", "abs_delta", time], kind="stable")
    anchor = df.groupby(event).head(1).set_index(event)[[time, "delta_hours_prophylaxis"]]

    df = df.join(anchor[time].rename("anchor_time"), on=event)
    near = df[(df[time] - df["anchor_time"]).abs() <= pd.Timedelta(minutes=pr["combine_minutes"])]
    has_prophylactic = near.groupby(event)["is_therapeutic"].transform(lambda s: (~s).any())
    course = near[~(has_prophylactic & near["is_therapeutic"])].copy()
    course["agent_class"] = agent_class(course[med], cfg)

    summary = course.groupby(event).agg(
        prophylaxis=(med, lambda s: " | ".join(sorted(set(s)))),
        n_prophylaxis_agents=(med, "nunique"),
        classes=("agent_class", lambda s: set(s.where(s.notna(), None))),
    )
    summary["prophylaxis_group"] = summary["classes"].apply(lambda c: group_prophylaxis(c, cfg))
    summary["delta_hours_prophylaxis"] = anchor["delta_hours_prophylaxis"]
    unclassified = course.loc[course["agent_class"].isna(), med].value_counts()
    if len(unclassified):
        log.info("choose_prophylaxis: agents with no class (course grouped as other unless another rule applies): %s",
                 unclassified.to_dict())
    return summary.reset_index()[out_cols]


def add_prophylaxis(episodes: pd.DataFrame, courses: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Attach the prophylaxis of each episode's attributed surgery.

    Grain: one row per infection episode (unchanged). Surgeries without a
    candidate dose get ``prophylaxis_group`` = the 'none' label (absence of a
    record = no prophylaxis) and missing ``delta_hours_prophylaxis``.
    """
    event = "surgery_event_id"
    if event not in episodes.columns:
        raise ValueError(f"add_prophylaxis: episodes has no '{event}'; check config.yaml: episode_table")
    clash = [c for c in courses.columns if c != event and c in episodes.columns]
    if clash:
        raise ValueError(f"add_prophylaxis: episodes already has column(s) {clash}")
    if not courses[event].is_unique:
        raise ValueError("add_prophylaxis: more than one course per surgery")
    out = episodes.merge(courses, on=event, how="left", validate="many_to_one")
    none = cfg["prophylaxis"]["group_labels"]["none"]
    out["prophylaxis_group"] = out["prophylaxis_group"].fillna(none)
    out["n_prophylaxis_agents"] = out["n_prophylaxis_agents"].fillna(0).astype(int)
    log.info("add_prophylaxis: prophylaxis groups %s", out["prophylaxis_group"].value_counts().to_dict())
    return out
