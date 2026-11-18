"""Tests for attributing each sample to its most recent preceding surgery (``esbl_rf.surgery``).

Rule (analysis_plan section 2): a sample is attributed to the most recent
surgery in the same spell that started strictly before it. ``n_surgeries_before``
counts the spell's surgeries before the sample.
"""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.surgery import attribute_surgery

EC = "escherichia coli"


def make_samples(rows):
    """Rows of (subject, spell, lab_test_id, collect_ts)."""
    df = pd.DataFrame(rows, columns=["subject", "spell_identifier", "lab_test_id", "collect_ts"])
    df["collect_ts"] = pd.to_datetime(df["collect_ts"])
    df["organism_bug"] = EC
    return df


def make_surgeries(rows, cfg):
    """Rows of (subject, spell, anchor_ts); other surgery columns get dummy values."""
    df = pd.DataFrame(rows, columns=["subject", "spell_identifier", "surgery_anchor_ts"])
    df["surgery_anchor_ts"] = pd.to_datetime(df["surgery_anchor_ts"])
    for i, col in enumerate(c for c in cfg["columns"]["surgery_columns"] if c != "surgery_anchor_ts"):
        df[col] = f"{col}_{i}" if not col.endswith("_ts") else df["surgery_anchor_ts"]
    df["surgery_event_id"] = range(len(df))
    return df


def test_sample_after_one_surgery_is_attributed(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-10")])
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert out["surgery_anchor_ts"].iloc[0] == pd.Timestamp("2020-01-05")
    assert out["n_surgeries_before"].iloc[0] == 1
    assert out["days_from_surgery_to_sample"].iloc[0] == pytest.approx(5.0)


def test_most_recent_of_two_surgeries_is_used(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-20")])
    g = make_surgeries([("p1", "a", "2020-01-02"), ("p1", "a", "2020-01-15")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert out["surgery_anchor_ts"].iloc[0] == pd.Timestamp("2020-01-15")
    assert out["n_surgeries_before"].iloc[0] == 2


def test_later_surgery_is_not_attributed(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-10")])
    g = make_surgeries([("p1", "a", "2020-01-02"), ("p1", "a", "2020-01-15")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert out["surgery_anchor_ts"].iloc[0] == pd.Timestamp("2020-01-02")
    assert out["n_surgeries_before"].iloc[0] == 1


def test_sample_before_all_surgeries_is_preoperative(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-01")])
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert pd.isna(out["surgery_anchor_ts"].iloc[0])
    assert out["n_surgeries_before"].iloc[0] == 0
    assert pd.isna(out["days_from_surgery_to_sample"].iloc[0])


def test_sample_at_the_exact_surgery_time_is_not_postoperative(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-05 10:00")])
    g = make_surgeries([("p1", "a", "2020-01-05 10:00")], cfg)
    assert pd.isna(attribute_surgery(s, g, cfg)["surgery_anchor_ts"].iloc[0])


def test_other_spells_and_patients_are_ignored(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-10")])
    g = make_surgeries([("p1", "b", "2020-01-05"), ("p2", "a", "2020-01-06")], cfg)
    assert pd.isna(attribute_surgery(s, g, cfg)["surgery_anchor_ts"].iloc[0])


def test_rows_order_and_index_are_unchanged(cfg):
    s = make_samples([("p1", "a", 2, "2020-01-20"), ("p1", "a", 1, "2020-01-03"), ("p1", "a", 3, "2020-01-10")])
    s.index = [9, 4, 6]
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert list(out.index) == [9, 4, 6]
    assert list(out["lab_test_id"]) == [2, 1, 3]
    assert out["n_surgeries_before"].tolist() == [1, 0, 1]


def test_carried_columns_come_from_the_attributed_surgery(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-20")])
    g = make_surgeries([("p1", "a", "2020-01-02"), ("p1", "a", "2020-01-15")], cfg)
    out = attribute_surgery(s, g, cfg)
    assert out["surgery_event_id"].iloc[0] == 1  # the second surgery


def test_surgery_column_already_in_samples_raises(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-10")]).assign(procedure_desc="x")
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    with pytest.raises(ValueError, match="procedure_desc"):
        attribute_surgery(s, g, cfg)


def test_missing_collection_time_raises(cfg):
    s = make_samples([("p1", "a", 1, None)])
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    with pytest.raises(ValueError, match=r"attribute_surgery.*1"):
        attribute_surgery(s, g, cfg)


def test_duplicate_surgery_rows_raise(cfg):
    s = make_samples([("p1", "a", 1, "2020-01-10")])
    g = make_surgeries([("p1", "a", "2020-01-05")], cfg)
    g = pd.concat([g, g], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        attribute_surgery(s, g, cfg)
