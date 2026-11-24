"""Table 1 and its supplement: descriptive summaries by ESBL status (analysis plan sections 8.1 and 9).

Ported from the manuscript's ``scripts/get_table1.py``, which called ``tableone``.
CHANGE: the summary is computed here as numbers (counts, denominators, medians,
quartiles) instead of tableone's formatted strings, so the export step can
apply small-number suppression to the counts before formatting. No p-values
(decided 2026-11-13). The baseline's string clean-up (``'none'``, ``'unknown'``
to missing) and gender/ethnicity recodes are not needed: earlier steps already
produce clean values.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

GROUPS = ("overall", "esbl_pos", "non_esbl")
_STATS = ("n", "denom", "missing", "median", "q1", "q3")


def _group_masks(episodes: pd.DataFrame, cfg: dict) -> dict[str, pd.Series]:
    outcome = cfg["columns"]["outcome"]
    if outcome not in episodes.columns:
        raise ValueError(f"summarise_table: episodes has no outcome column '{outcome}'")
    status = episodes[outcome]
    bad = status.isna() | ~status.isin([0, 1])
    if bad.any():
        raise ValueError(f"summarise_table: {int(bad.sum())} episode(s) have an outcome that is not 0/1")
    return {"overall": pd.Series(True, index=episodes.index), "esbl_pos": status == 1, "non_esbl": status == 0}


def level_order(values: pd.Series, variable: str, cfg: dict) -> list:
    """Levels of a categorical variable: reference level first, then by descending count (ties by name).

    ``cci_group`` keeps the order of ``cci.group_labels``.
    """
    present = values.dropna()
    if variable == "cci_group":
        return [g for g in cfg["cci"]["group_labels"] if g in set(present)]
    counts = present.value_counts()
    ordered = sorted(counts.index, key=lambda lvl: (-counts[lvl], str(lvl)))
    ref = cfg["logistic_regression"]["reference_levels"].get(variable)
    if ref in ordered:
        ordered.remove(ref)
        ordered.insert(0, ref)
    return ordered


def _row(block: str, variable: str, level, kind: str) -> dict:
    return {"block": block, "variable": variable, "level": level, "kind": kind}


def summarise_table(episodes: pd.DataFrame, blocks: dict[str, list[str]], cfg: dict) -> pd.DataFrame:
    """Summarise each variable by ESBL status, as numbers (not formatted).

    Grain: input one row per infection episode; output one row per table row:
    a first ``N`` row (episodes per group), then per variable either one row
    (continuous: median, quartiles; binary: episodes with the flag) or one row
    per level (categorical). Columns ``<group>_n``, ``<group>_denom`` (non-missing
    episodes), ``<group>_missing``, ``<group>_median``, ``<group>_q1``,
    ``<group>_q3`` for each group in ``GROUPS``. Percentages use the non-missing
    denominator; missing values are counted, not shown as a level.

    Raises
    ------
    ValueError
        If a column is absent, the outcome is not 0/1, a binary flag has values
        other than 0/1, or a continuous column holds non-numeric values.
    """
    t1 = cfg["table1"]
    listed = [c for cols in blocks.values() for c in cols]
    missing = [c for c in listed if c not in episodes.columns]
    if missing:
        raise ValueError(f"summarise_table: episodes is missing column(s) {missing}")
    masks = _group_masks(episodes, cfg)

    rows = [{**_row("", "N", None, "total"), **{f"{g}_n": int(m.sum()) for g, m in masks.items()}}]
    for block, columns in blocks.items():
        for var in columns:
            values = episodes[var]
            if var in t1["categorical"]:
                kind = "categorical"
                levels = level_order(values, var, cfg)
            elif var in t1["binary"]:
                kind = "binary"
                bad = values.notna() & ~values.isin([0, 1])
                if bad.any():
                    raise ValueError(f"summarise_table: binary column '{var}' has {int(bad.sum())} value(s) other than 0/1")
                levels = [1]
            else:
                kind = "continuous"
                numeric = pd.to_numeric(values, errors="coerce")
                n_text = int((numeric.isna() & values.notna()).sum())
                if n_text:
                    raise ValueError(f"summarise_table: continuous column '{var}' has {n_text} non-numeric value(s)")
                values, levels = numeric, [None]
            for level in levels:
                row = _row(block, var, level, kind)
                for g, m in masks.items():
                    v = values[m]
                    row[f"{g}_denom"] = int(v.notna().sum())
                    row[f"{g}_missing"] = int(v.isna().sum())
                    if kind == "continuous":
                        row[f"{g}_n"] = row[f"{g}_denom"]
                        q = v.quantile([0.25, 0.5, 0.75]) if row[f"{g}_denom"] else pd.Series([float("nan")] * 3)
                        row[f"{g}_q1"], row[f"{g}_median"], row[f"{g}_q3"] = q.tolist()
                    else:
                        row[f"{g}_n"] = int((v == level).sum())
                rows.append(row)

    columns = ["block", "variable", "level", "kind"] + [f"{g}_{s}" for g in GROUPS for s in _STATS]
    out = pd.DataFrame(rows).reindex(columns=columns)
    for c in [f"{g}_{s}" for g in GROUPS for s in ("n", "denom", "missing")]:
        out[c] = out[c].astype("Int64")
    return out


def table1_blocks(cfg: dict) -> dict[str, list[str]]:
    """Row blocks of the main Table 1 (``table1.blocks``)."""
    return dict(cfg["table1"]["blocks"])


def supplement_blocks(cfg: dict) -> dict[str, list[str]]:
    """The supplementary table: all 17 Charlson conditions in one block."""
    return {"Charlson comorbidities": list(cfg["table1"]["supplementary_conditions"])}


def log_sparse_levels(table: pd.DataFrame, cfg: dict) -> None:
    """Warn about categorical levels with fewer than ``demographics.ethnicity_min_count`` episodes.

    For the analyst's check (e.g. the size of Mixed ethnicity, decided 2026-11-23);
    nothing is changed.
    """
    limit = cfg["demographics"]["ethnicity_min_count"]
    sparse = table[(table["kind"] == "categorical") & (table["overall_n"] < limit)]
    for _, r in sparse.iterrows():
        log.warning("table1: level '%s' of '%s' has fewer than %d episodes", r["level"], r["variable"], limit)
