"""Tests for attributing each sample to its most recent preceding surgery (``esbl_rf.surgery``).

Rule (analysis_plan section 2): a sample is attributed to the most recent
surgery in the same spell that started strictly before it. ``n_surgeries_before``
counts the spell's surgeries before the sample.
"""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.surgery import attribute_surgery, check_specialty

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


# --- clean_surgeries -------------------------------------------------------------

from esbl_rf.surgery import clean_surgeries

META, REAL = "other operative procedure (surg)", "total knee replacement"


def procs(cfg, rows):
    """Rows of (subject, spell, procedure_desc)."""
    return pd.DataFrame(rows, columns=["subject", "spell_identifier", cfg["columns"]["procedure"]])


def test_meta_label_dropped_when_spell_has_a_real_procedure(cfg):
    out = clean_surgeries(procs(cfg, [("p1", "a", META), ("p1", "a", REAL)]), cfg)
    assert out["procedure_desc"].tolist() == [REAL]


def test_meta_label_kept_when_it_is_the_only_procedure_in_the_spell(cfg):
    out = clean_surgeries(procs(cfg, [("p1", "a", META), ("p1", "a", "not in enumerations list")]), cfg)
    assert len(out) == 2


def test_spells_are_judged_separately(cfg):
    df = procs(cfg, [("p1", "a", META), ("p1", "a", REAL), ("p1", "b", META), ("p2", "a", META)])
    out = clean_surgeries(df, cfg)
    assert out[["subject", "spell_identifier"]].apply(tuple, axis=1).tolist() == [("p1", "a"), ("p1", "b"), ("p2", "a")]


def test_labels_are_matched_ignoring_case_and_spaces(cfg):
    out = clean_surgeries(procs(cfg, [("p1", "a", "  Other Operative Procedure (SURG) "), ("p1", "a", REAL)]), cfg)
    assert len(out) == 1


def test_missing_procedure_counts_as_real(cfg):
    out = clean_surgeries(procs(cfg, [("p1", "a", META), ("p1", "a", None)]), cfg)
    assert len(out) == 1 and pd.isna(out["procedure_desc"].iloc[0])


def test_order_and_index_are_kept(cfg):
    df = procs(cfg, [("p1", "a", REAL), ("p1", "a", META), ("p2", "a", REAL)])
    df.index = [8, 2, 5]
    assert list(clean_surgeries(df, cfg).index) == [8, 5]


def test_emergency_label_raises_with_count(cfg):
    df = procs(cfg, [("p1", "a", "emergency ncepod (surg)"), ("p1", "a", "Emergency laparotomy")])
    with pytest.raises(ValueError, match=r"clean_surgeries.*2.*emergency"):
        clean_surgeries(df, cfg)


def test_missing_column_raises(cfg):
    with pytest.raises(ValueError, match="procedure_desc"):
        clean_surgeries(procs(cfg, [("p1", "a", REAL)]).drop(columns="procedure_desc"), cfg)


# --- map_specialty ---------------------------------------------------------------

from esbl_rf.surgery import map_specialty


def test_specialty_is_mapped_ignoring_case_and_spaces(cfg):
    df = pd.DataFrame({"treatment_function_code_des": ["  Colorectal Surgery ", "UROLOGY", "ENT"]}, index=[3, 1, 2])
    out = map_specialty(df, cfg)
    assert out["tfc_group"].tolist() == ["abdominal_gi", "uro_nephro", "neuro_ent"]
    assert list(out.index) == [3, 1, 2]


def test_unmapped_and_missing_specialty_are_left_missing_and_counted(cfg, caplog):
    df = pd.DataFrame({"treatment_function_code_des": ["dermatology", "dermatology", None, "urology"]})
    with caplog.at_level("INFO"):
        out = map_specialty(df, cfg)
    assert out["tfc_group"].isna().tolist() == [True, True, True, False]
    assert "dermatology" in caplog.text and "2" in caplog.text


def test_specialty_column_missing_raises(cfg):
    with pytest.raises(ValueError, match="treatment_function_code_des"):
        map_specialty(pd.DataFrame({"x": [1]}), cfg)


# --- add_surgery_length ------------------------------------------------------------
from esbl_rf.surgery import add_surgery_length


def timed(rows):
    df = pd.DataFrame(rows, columns=["surgery_start_ts", "surgery_stop_ts"])
    return df.apply(pd.to_datetime)


def test_surgery_length_in_hours(cfg):
    out = add_surgery_length(timed([("2020-01-01 08:00", "2020-01-01 10:30")]), cfg)
    assert out["surgery_length_hours"].tolist() == [2.5]


def test_surgery_length_missing_time_gives_missing(cfg):
    out = add_surgery_length(timed([("2020-01-01 08:00", None), (None, "2020-01-01 10:30")]), cfg)
    assert out["surgery_length_hours"].isna().all()


def test_surgery_length_negative_set_missing_and_warned(cfg, caplog):
    with caplog.at_level("WARNING"):
        out = add_surgery_length(timed([("2020-01-01 10:00", "2020-01-01 08:00"), ("2020-01-01 08:00", "2020-01-01 09:00")]), cfg)
    assert out["surgery_length_hours"].isna().tolist() == [True, False]
    assert "1 surgery row" in caplog.text


def test_surgery_length_keeps_rows_and_missing_column_raises(cfg):
    df = timed([("2020-01-01 08:00", "2020-01-01 09:00")])
    assert len(add_surgery_length(df, cfg)) == 1
    with pytest.raises(ValueError, match="missing column"):
        add_surgery_length(df.drop(columns="surgery_stop_ts"), cfg)


def test_concurrent_procedures_count_as_one_surgery(cfg):
    """Two procedures of one surgery event are one surgery, and the attributed row does not depend on order."""
    s = make_samples([("p1", "a", 1, "2020-01-20")])
    g = make_surgeries([("p1", "a", "2020-01-02"), ("p1", "a", "2020-01-15"), ("p1", "a", "2020-01-15")], cfg)
    g["surgery_event_id"] = [10, 20, 20]
    g["procedure_desc"] = ["hernia repair", "colectomy", "stoma formation"]
    out = attribute_surgery(s, g, cfg)
    flipped = attribute_surgery(s, g.iloc[::-1], cfg)
    assert out["n_surgeries_before"].iloc[0] == 2
    assert out["procedure_desc"].iloc[0] == flipped["procedure_desc"].iloc[0] == "stoma formation"


def test_check_specialty_splits_empty_from_unmapped(cfg):
    eps = pd.DataFrame({"tfc": ["vascular", None, None, None, None],
                        "tfc_desc": ["Vascular Surgery", None, "none", "Dermatology", "dermatology "]})
    out = check_specialty(eps, cfg).set_index(["reason", "tfc_desc"])["n_episodes"]
    assert out.xs("no description", level="reason").sum() == 2
    assert out[("description not in tfc_groups", "dermatology")] == 2
    assert out.sum() == 4
