"""Tests for ESBL labelling (``esbl_rf.outcome``).

A sample is ESBL-positive (1) if any of cefotaxime, ceftazidime, ceftriaxone or
cefpodoxime is in the resistant lookup, the ESBL marker is positive, or the lab
reports the ESBL-producer statement. Non-ESBL (0) if (cefotaxime and
ceftazidime) or (ceftriaxone and ceftazidime) are both susceptible, cefpodoxime
is susceptible, or the marker is negative. Otherwise unknown (<NA>).

CHANGE vs baseline: the lookups are matched as "drug : result" pairs. The
baseline kept only the result part, so a value such as '1' listed as resistant
for ceftriaxone also made ceftazidime '1' resistant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from esbl_rf.outcome import build_ast_rules, label_esbl_status

R = ["ceftriaxone : resistant", "ceftriaxone : 4", "ceftazidime : resistant", "cefotaxime : resistant",
     "cefpodoxime : resistant"]
S = ["ceftriaxone : sensitive", "ceftazidime : sensitive", "ceftazidime : 4", "cefotaxime : sensitive",
     "cefpodoxime : sensitive"]
COLS = ["cefotaxime", "ceftazidime", "ceftriaxone", "cefpodoxime", "esbl markers (ss = present)", "esbl_statement"]


@pytest.fixture
def rules():
    return build_ast_rules(pd.DataFrame({"sensitivity": R}), pd.DataFrame({"sensitivity": S}))


def label(cfg, rules, **row) -> object:
    """Label one sample; unspecified columns are missing, esbl_statement is 0."""
    data = {c: [np.nan] for c in COLS}
    data["esbl_statement"] = [0]
    for key, val in row.items():
        data[key.replace("marker", "esbl markers (ss = present)")] = [val]
    return label_esbl_status(pd.DataFrame(data), *rules, cfg)["esbl_status"].iloc[0]


def test_resistant_third_gen_is_esbl(cfg, rules):
    assert label(cfg, rules, ceftriaxone="resistant") == 1


def test_cefpodoxime_resistant_is_esbl(cfg, rules):
    assert label(cfg, rules, cefpodoxime="resistant") == 1


def test_marker_positive_is_esbl(cfg, rules):
    assert label(cfg, rules, marker="sensitive") == 1


def test_lab_esbl_statement_is_esbl_without_ast(cfg, rules):
    assert label(cfg, rules, esbl_statement=1) == 1


def test_lab_esbl_statement_beats_susceptible_results(cfg, rules):
    assert label(cfg, rules, esbl_statement=1, cefotaxime="sensitive", ceftazidime="sensitive") == 1


def test_resistant_wins_over_susceptible(cfg, rules):
    assert label(cfg, rules, ceftriaxone="resistant", ceftazidime="sensitive") == 1


def test_cefotaxime_and_ceftazidime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, cefotaxime="sensitive", ceftazidime="sensitive") == 0


def test_ceftriaxone_and_ceftazidime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, ceftriaxone="sensitive", ceftazidime="sensitive") == 0


def test_cefpodoxime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, cefpodoxime="sensitive") == 0


def test_marker_resistant_is_non_esbl(cfg, rules):
    assert label(cfg, rules, marker="resistant") == 0


def test_one_susceptible_drug_without_its_partner_is_unknown(cfg, rules):
    assert pd.isna(label(cfg, rules, ceftriaxone="sensitive"))


def test_no_results_is_unknown(cfg, rules):
    assert pd.isna(label(cfg, rules))


def test_matching_is_per_drug(cfg, rules):
    """'4' is resistant for ceftriaxone but susceptible for ceftazidime."""
    assert label(cfg, rules, ceftriaxone="4") == 1
    assert label(cfg, rules, ceftazidime="4", cefotaxime="sensitive") == 0


def test_case_and_whitespace_are_ignored(cfg, rules):
    assert label(cfg, rules, ceftriaxone="  Resistant ") == 1


def test_rows_and_index_are_unchanged(cfg, rules):
    df = pd.DataFrame({c: [np.nan, np.nan] for c in COLS}, index=[7, 3])
    df["esbl_statement"] = [1, 0]
    out = label_esbl_status(df, *rules, cfg)
    assert list(out.index) == [7, 3]
    assert out["esbl_status"].tolist()[0] == 1 and pd.isna(out["esbl_status"].iloc[1])


def test_rule_in_both_lookups_raises(cfg):
    with pytest.raises(ValueError, match="both"):
        build_ast_rules(pd.DataFrame({"sensitivity": ["ceftriaxone : 4"]}),
                        pd.DataFrame({"sensitivity": ["ceftriaxone : 4"]}))


def test_lookup_string_without_colon_raises(cfg):
    with pytest.raises(ValueError, match="resistant"):
        build_ast_rules(pd.DataFrame({"sensitivity": ["resistant"]}), pd.DataFrame({"sensitivity": S}))


def test_missing_ast_column_raises(cfg, rules):
    df = pd.DataFrame({"cefotaxime": ["resistant"], "esbl_statement": [0]})
    with pytest.raises(ValueError, match="ceftriaxone"):
        label_esbl_status(df, *rules, cfg)


def test_committed_lookups_are_valid(cfg):
    """The tracked 3GCR/3GCS files parse and do not overlap."""
    from esbl_rf.io import read_lookup
    r = read_lookup(cfg, "third_gen_ceph_resistant")
    s = read_lookup(cfg, "third_gen_ceph_susceptible")
    r_rules, s_rules = build_ast_rules(r, s)
    assert len(r_rules) == len(r) and len(s_rules) == len(s)  # no duplicates
