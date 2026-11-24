"""Statistical disclosure control for everything written to ``outputs/export/``.

Rules (CLAUDE.md, decided 2026-11-24):

- **Primary suppression:** a count from 1 to ``suppression_threshold - 1`` is
  shown as ``<5`` (for a threshold of 5) and its percentage is hidden. Zero is
  shown: an empty cell is not disclosive.
- **Secondary suppression:** cells that add up to a total someone can see (the
  levels of a variable, or ESBL-positive + non-ESBL = overall) form a *linked
  set*. If exactly one cell in a set is hidden, it could be recovered by
  subtraction, so the smallest other cell in the set is hidden too (shown as
  ``*``). This repeats until no set has exactly one hidden cell.
- **Rounding:** sequential counts (the flowchart, complete-case loss), where any
  exclusion is the difference of two shown totals, are rounded to the nearest
  ``rounding_base`` instead; values 1 to threshold - 1 are shown as ``<5``.
"""
from __future__ import annotations

from collections.abc import Hashable, Iterable

import pandas as pd

Cell = tuple[Hashable, str]   # (row label, column name)
PRIMARY, SECONDARY = "<{t}", "*"


def primary_mask(values: pd.DataFrame, threshold: int) -> pd.DataFrame:
    """True where a count is from 1 to ``threshold - 1`` (missing values are never hidden)."""
    numeric = values.apply(pd.to_numeric, errors="coerce")
    return (numeric >= 1) & (numeric < threshold)


def secondary_mask(values: pd.DataFrame, hidden: pd.DataFrame, linked_sets: Iterable[list[Cell]]) -> pd.DataFrame:
    """Extend ``hidden`` so that no linked set has exactly one hidden cell.

    Parameters
    ----------
    values : pd.DataFrame
        The counts.
    hidden : pd.DataFrame
        Boolean mask from ``primary_mask`` (same shape); not modified.
    linked_sets : iterable of list of (row, column)
        Cells whose sum is visible or can be derived. Cells with a missing value
        are ignored.

    Returns
    -------
    pd.DataFrame
        Mask of cells hidden by secondary suppression only.
    """
    sets = [[c for c in s if pd.notna(values.at[c[0], c[1]])] for s in linked_sets]
    mask = hidden.copy()
    changed = True
    while changed:
        changed = False
        for cells in sets:
            shown = [c for c in cells if not mask.at[c[0], c[1]]]
            if len(cells) - len(shown) == 1 and shown:
                nxt = min(shown, key=lambda c: (values.at[c[0], c[1]] == 0, values.at[c[0], c[1]]))
                mask.at[nxt[0], nxt[1]] = True
                changed = True
    return mask & ~hidden


def suppress(values: pd.DataFrame, threshold: int,
             linked_sets: Iterable[list[Cell]] = ()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Primary and secondary masks for ``values`` (see the module docstring)."""
    primary = primary_mask(values, threshold)
    return primary, secondary_mask(values, primary, list(linked_sets))


def show_count(value, primary: bool, secondary: bool, threshold: int) -> str:
    """Text for one count cell: ``<5``, ``*`` or the integer."""
    if pd.isna(value):
        return ""
    if primary:
        return PRIMARY.format(t=threshold)
    if secondary:
        return SECONDARY
    return f"{int(value)}"


def suppress_counts(series: pd.Series, threshold: int) -> pd.Series:
    """Primary suppression only, for counts with no visible total (as text)."""
    return series.apply(lambda v: "" if pd.isna(v) else
                        (PRIMARY.format(t=threshold) if 1 <= v < threshold else f"{int(v)}"))


def round_counts(series: pd.Series, threshold: int, base: int = 5) -> pd.Series:
    """Round counts to the nearest ``base`` (half up); 1 to ``threshold - 1`` shown as ``<threshold``; as text."""
    def one(v):
        if pd.isna(v):
            return ""
        v = int(v)
        if 1 <= v < threshold:
            return PRIMARY.format(t=threshold)
        return f"{int(base * ((v + base / 2) // base))}"
    return series.apply(one)
