"""Tests for esbl_rf.suppression and the suppressed export tables."""
import pandas as pd

from esbl_rf.export import fmt_median_iqr, fmt_or, fmt_p, median_iqr_decimals, table1_display
from esbl_rf.suppression import round_counts, suppress, suppress_counts


def test_primary_hides_one_to_four_not_zero():
    values = pd.DataFrame({"a": [0, 1, 4, 5]})
    primary, secondary = suppress(values, 5)
    assert primary["a"].tolist() == [False, True, True, False] and not secondary.any().any()


def test_secondary_hides_next_smallest_in_linked_set():
    # overall = pos + neg; pos = 3 is hidden, so neg (20) could be recovered: hide the smaller of the others
    values = pd.DataFrame({"overall": [23], "pos": [3], "neg": [20]})
    primary, secondary = suppress(values, 5, [[(0, "overall"), (0, "pos"), (0, "neg")]])
    assert primary.loc[0].tolist() == [False, True, False]
    assert secondary.loc[0].tolist() == [False, False, True]


def test_secondary_cascades_across_sets():
    # rows are levels of one variable; columns overall/pos/neg. Level b pos = 2.
    values = pd.DataFrame({"overall": [50, 12, 40], "pos": [20, 2, 18], "neg": [30, 10, 22]})
    rows = [[(i, c) for c in values.columns] for i in values.index]
    cols = [[(i, c) for i in values.index] for c in values.columns]
    primary, secondary = suppress(values, 5, rows + cols)
    hidden = primary | secondary
    for s in rows + cols:
        assert sum(hidden.at[i, c] for i, c in s) != 1


def test_rounding_and_counts_as_text():
    assert round_counts(pd.Series([0, 3, 7, 12, 13, None]), 5).tolist() == ["0", "<5", "5", "10", "15", ""]
    assert suppress_counts(pd.Series([0, 4, 5]), 5).tolist() == ["0", "<5", "5"]


def test_number_formats():
    assert fmt_or(1.234, 0.987, 1.5) == "1.23 (0.99–1.50)" and fmt_or(float("nan"), 1, 1) == "–"
    assert fmt_p(0.0004) == "<0.001" and fmt_p(0.0456) == "0.046"
    assert fmt_median_iqr(1.0, 1.0, 2.0) == "1 [1–2]" and fmt_median_iqr(38.0, 37.4, 38.5) == "38.0 [37.4–38.5]"
    assert fmt_median_iqr(154.0, 78.5, 226.0) == "154 [78–226]"
    assert median_iqr_decimals([5.0, 3.0, 8.0, 6.0, 3.5, 8.0]) == 1      # one group not whole: whole row 1 dp


def test_table1_display_hides_small_cells_and_their_percent(cfg):
    t1 = pd.DataFrame([
        {"block": "", "variable": "N", "level": None, "kind": "total", "overall_n": 60, "esbl_pos_n": 20, "non_esbl_n": 40},
        *[{"block": "Infection", "variable": "organism_bug", "level": lvl, "kind": "categorical",
           "overall_n": o, "esbl_pos_n": p, "non_esbl_n": n, "overall_denom": 60, "esbl_pos_denom": 20,
           "non_esbl_denom": 40, "overall_missing": 0}
          for lvl, o, p, n in [("escherichia coli", 30, 10, 20), ("klebsiella pneumoniae", 27, 7, 20),
                               ("proteus mirabilis", 3, 3, 0)]],
    ])
    table, notes = table1_display(t1, cfg)
    pm = table[table["Characteristic"] == "  P. mirabilis"].iloc[0]
    assert pm["Overall (N=60)"] == "<5" and pm["ESBL-positive (N=20)"] == "<5"
    # the ESBL-positive column could be worked out from 20 - 10 - 7: another cell in it is hidden
    pos = table["ESBL-positive (N=20)"]
    assert (pos == "*").sum() + (pos == "<5").sum() >= 2
    assert not table.apply(lambda c: c.astype(str).str.contains(r"<5 \(")).any().any()
