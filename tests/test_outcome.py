"""Tests for ESBL labelling (``esbl_rf.outcome``).

A sample is ESBL-positive (1) if any of cefotaxime, ceftazidime, ceftriaxone or
cefpodoxime is in the resistant lookup, the ESBL marker is positive, or the lab
reports the ESBL-producer statement. Non-ESBL (0) if any third-generation
cephalosporin is susceptible (surveillance rule, decided 2026-11-26) or the
marker is negative. Otherwise unknown (<NA>).

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
     "cefpodoxime : resistant", "cefixime : resistant", "cephalexin : resistant"]
S = ["ceftriaxone : sensitive", "ceftazidime : sensitive", "ceftazidime : 4", "cefotaxime : sensitive",
     "cefpodoxime : sensitive", "cephalexin : sensitive"]
MARK = "esbl markers (ss = present)"


@pytest.fixture
def rules():
    return build_ast_rules(pd.DataFrame({"sensitivity": R}), pd.DataFrame({"sensitivity": S}))


def label(cfg, rules, results=(), esbl_statement=0):
    """Label one sample (subject p1, lab test 1, E. coli) from a list of result strings."""
    sample = pd.DataFrame({"subject": ["p1"], "lab_test_id": [1], "organism_bug": ["escherichia coli"],
                           "esbl_statement": [esbl_statement]})
    res = pd.DataFrame({"subject": "p1", "lab_test_id": 1, "organism_bug": "escherichia coli",
                        "sensitivity": list(results)})
    return label_esbl_status(sample, res, rules, cfg)["esbl_status"].iloc[0]


def test_resistant_third_gen_is_esbl(cfg, rules):
    assert label(cfg, rules, ["ceftriaxone : resistant"]) == 1


def test_cefpodoxime_resistant_is_esbl(cfg, rules):
    assert label(cfg, rules, ["cefpodoxime : resistant"]) == 1


def test_cefixime_resistant_is_esbl(cfg, rules):
    """CHANGE vs baseline (decided 2026-11-17): cefixime counts."""
    assert label(cfg, rules, ["cefixime : resistant"]) == 1


def test_marker_positive_is_esbl(cfg, rules):
    assert label(cfg, rules, [f"{MARK} : sensitive"]) == 1


def test_lab_esbl_statement_is_esbl_without_ast(cfg, rules):
    assert label(cfg, rules, [], esbl_statement=1) == 1


def test_lab_esbl_statement_beats_susceptible_results(cfg, rules):
    assert label(cfg, rules, ["cefotaxime : sensitive", "ceftazidime : sensitive"], esbl_statement=1) == 1


def test_resistant_wins_over_susceptible(cfg, rules):
    assert label(cfg, rules, ["ceftriaxone : resistant", "ceftazidime : sensitive"]) == 1


def test_cefotaxime_and_ceftazidime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, ["cefotaxime : sensitive", "ceftazidime : sensitive"]) == 0


def test_ceftriaxone_and_ceftazidime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, ["ceftriaxone : sensitive", "ceftazidime : sensitive"]) == 0


def test_cefpodoxime_susceptible_is_non_esbl(cfg, rules):
    assert label(cfg, rules, ["cefpodoxime : sensitive"]) == 0


def test_marker_resistant_is_non_esbl(cfg, rules):
    assert label(cfg, rules, [f"{MARK} : resistant"]) == 0


@pytest.mark.parametrize("result", ["ceftriaxone : sensitive", "cefotaxime : sensitive", "ceftazidime : sensitive"])
def test_one_susceptible_third_gen_is_non_esbl(cfg, rules, result):
    """Surveillance rule (decided 2026-11-26): one susceptible 3GC is enough."""
    assert label(cfg, rules, [result]) == 0


def test_strict_rule_needs_the_partner(cfg, rules):
    """The 2026-11-17 rule, kept for the sensitivity analysis: ceftriaxone S alone is unknown."""
    strict = {**cfg, "outcome": {**cfg["outcome"], "susceptible_alone_drugs": ["cefpodoxime"],
                                 "susceptible_pairs": [["cefotaxime", "ceftazidime"], ["ceftriaxone", "ceftazidime"]]}}
    assert pd.isna(label(strict, rules, ["ceftriaxone : sensitive"]))
    assert label(strict, rules, ["ceftriaxone : sensitive", "ceftazidime : sensitive"]) == 0


def test_no_results_is_unknown(cfg, rules):
    assert pd.isna(label(cfg, rules, []))


def test_strings_in_neither_lookup_are_unknown(cfg, rules):
    assert pd.isna(label(cfg, rules, ["ceftazidime : not done", f"{MARK} : not determined."]))


def test_matching_is_per_drug(cfg, rules):
    """'4' is resistant for ceftriaxone but susceptible for ceftazidime."""
    assert label(cfg, rules, ["ceftriaxone : 4"]) == 1
    assert label(cfg, rules, ["ceftazidime : 4", "cefotaxime : sensitive"]) == 0


def test_case_and_spacing_are_ignored(cfg, rules):
    assert label(cfg, rules, ["  Ceftriaxone:Resistant "]) == 1


def test_evidence_does_not_leak_between_samples(cfg, rules):
    samples = pd.DataFrame({"subject": ["p1", "p1"], "lab_test_id": [1, 2], "organism_bug": ["escherichia coli"] * 2,
                            "esbl_statement": [0, 0]}, index=[7, 3])
    res = pd.DataFrame({"subject": ["p1", "p1"], "lab_test_id": [1, 2], "organism_bug": ["escherichia coli"] * 2,
                        "sensitivity": ["ceftriaxone : resistant", "cefpodoxime : sensitive"]})
    out = label_esbl_status(samples, res, rules, cfg)
    assert list(out.index) == [7, 3]
    assert out["esbl_status"].tolist() == [1, 0]


def test_duplicate_sample_key_raises(cfg, rules):
    samples = pd.DataFrame({"subject": ["p1"] * 2, "lab_test_id": [1, 1], "organism_bug": ["escherichia coli"] * 2,
                            "esbl_statement": [0, 0]})
    res = pd.DataFrame(columns=["subject", "lab_test_id", "organism_bug", "sensitivity"])
    with pytest.raises(ValueError, match="duplicate sample key"):
        label_esbl_status(samples, res, rules, cfg)


def test_result_without_a_sample_raises(cfg, rules):
    samples = pd.DataFrame({"subject": ["p1"], "lab_test_id": [1], "organism_bug": ["escherichia coli"], "esbl_statement": [0]})
    res = pd.DataFrame({"subject": ["p1"], "lab_test_id": [99], "organism_bug": ["escherichia coli"],
                        "sensitivity": ["ceftriaxone : resistant"]})
    with pytest.raises(ValueError, match="no sample"):
        label_esbl_status(samples, res, rules, cfg)


def test_missing_column_raises(cfg, rules):
    samples = pd.DataFrame({"subject": ["p1"], "lab_test_id": [1], "organism_bug": ["escherichia coli"]})
    res = pd.DataFrame(columns=["subject", "lab_test_id", "organism_bug", "sensitivity"])
    with pytest.raises(ValueError, match="esbl_statement"):
        label_esbl_status(samples, res, rules, cfg)


def test_rule_in_both_lookups_raises(cfg):
    with pytest.raises(ValueError, match="both"):
        build_ast_rules(pd.DataFrame({"sensitivity": ["ceftriaxone : 4"]}),
                        pd.DataFrame({"sensitivity": ["ceftriaxone : 4"]}))


def test_lookup_string_without_colon_raises(cfg):
    with pytest.raises(ValueError, match="resistant"):
        build_ast_rules(pd.DataFrame({"sensitivity": ["resistant"]}), pd.DataFrame({"sensitivity": S}))


def test_committed_lookups_are_valid(cfg):
    """The tracked 3GCR/3GCS files parse and no string is listed twice."""
    from esbl_rf.io import read_lookup
    r = read_lookup(cfg, "third_gen_ceph_resistant")
    s = read_lookup(cfg, "third_gen_ceph_susceptible")
    assert len(build_ast_rules(r, s)) == len(r) + len(s)


def test_cefalexin_susceptible_is_non_esbl(cfg, rules):
    """Decided 2026-11-26: ESBLs hydrolyse cefalexin, so cefalexin S argues against ESBL."""
    assert label(cfg, rules, ["cephalexin : sensitive"]) == 0


def test_cefalexin_resistant_alone_is_unknown(cfg, rules):
    """Cefalexin R has other causes (e.g. AmpC), so it is not positive evidence."""
    assert pd.isna(label(cfg, rules, ["cephalexin : resistant"]))


def test_third_gen_resistance_beats_cefalexin_susceptible(cfg, rules):
    assert label(cfg, rules, ["cephalexin : sensitive", "ceftriaxone : resistant"]) == 1


def test_every_susceptible_alone_drug_has_word_results_in_the_real_lookup(cfg):
    """The lookup matches whole 'drug : result' strings, so each drug needs its own S rows."""
    from esbl_rf.io import read_lookup
    rules = build_ast_rules(read_lookup(cfg, "third_gen_ceph_resistant"), read_lookup(cfg, "third_gen_ceph_susceptible"))
    listed = set(read_lookup(cfg, "third_gen_ceph_susceptible").iloc[:, 0].str.strip())
    missing = [f"{d} : {r}" for d in cfg["outcome"]["susceptible_alone_drugs"] for r in ("sensitive", "susceptible")
               if f"{d} : {r}" not in listed]
    assert not missing, missing
    assert len(rules) > 0
