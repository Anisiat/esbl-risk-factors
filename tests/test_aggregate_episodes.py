"""Tests for collapsing samples to one row per infection episode (``esbl_rf.episodes``).

Rules (see the plan agreed 2026-11-18): status is 1 if any sample is ESBL, 0 if
at least one is non-ESBL and none is ESBL, missing if no sample has a status.
Site is the first of ``sites.hierarchy`` present. ``n_sites`` excludes ``other``.
Surgery variables come from the episode's first sample. Samples without a status
or from ``other`` sites still count for the span and ``n_samples``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.episodes import aggregate_episodes, exclude_episodes_without_status, exclude_other_site_episodes

DEFAULTS = {
    "subject": "p1", "spell_identifier": "s1", "organism_bug": "escherichia coli", "infection_id": "e1",
    "esbl_status": 0, "site_grouped": "urine",
    "admission_ts": pd.Timestamp("2020-01-01 08:00"), "discharge_ts": pd.Timestamp("2020-02-01 08:00"),
    "age_at_admission_n": 60, "index_of_multiple_deprivation_decile": 5,
    "surgery_start_ts": pd.Timestamp("2020-01-02 08:00"), "surgery_stop_ts": pd.Timestamp("2020-01-02 10:30"),
    "procedure_desc": "knee", "surgical_area": "ortho", "tfc_group": "ortho_plastics",
    "n_surgeries_before": 1, "days_from_surgery_to_sample": 3.0, "surgery_length_hours": 2.5,
}


def make(*rows):
    """Each row is a dict of overrides; ``collect_ts`` is required."""
    df = pd.DataFrame([{**DEFAULTS, **r} for r in rows])
    df["lab_test_id"] = range(len(df))
    df["collect_ts"] = pd.to_datetime(df["collect_ts"])
    df["esbl_status"] = df["esbl_status"].astype("Int64")
    return df


def agg(cfg, *rows):
    return aggregate_episodes(make(*rows), cfg).set_index("infection_id")


def test_one_row_per_episode_and_sample_count(cfg):
    out = aggregate_episodes(make({"collect_ts": "2020-01-05"}, {"collect_ts": "2020-01-06"},
                                  {"collect_ts": "2020-01-07", "infection_id": "e2"}), cfg)
    assert out["infection_id"].tolist() == ["e1", "e2"] and out["n_samples"].tolist() == [2, 1]


@pytest.mark.parametrize("statuses, expected", [
    ([1, 0], 1), ([1, None], 1), ([0, None], 0), ([0, 0], 0), ([None, None], None),
])
def test_episode_status_from_samples(cfg, statuses, expected):
    rows = [{"collect_ts": f"2020-01-0{5 + i}", "esbl_status": s} for i, s in enumerate(statuses)]
    value = agg(cfg, *rows).loc["e1", "esbl_status"]
    assert (pd.isna(value) if expected is None else value == expected)


@pytest.mark.parametrize("sites, expected", [
    (["urine", "blood"], "blood"), (["wound", "urine"], "urine"), (["other", "wound"], "wound"),
    (["other", "other"], "other"), (["tissue/biopsy", "sputum"], "sputum"),
])
def test_site_follows_the_hierarchy(cfg, sites, expected):
    rows = [{"collect_ts": f"2020-01-0{5 + i}", "site_grouped": s} for i, s in enumerate(sites)]
    assert agg(cfg, *rows).loc["e1", "site"] == expected


def test_n_sites_counts_distinct_real_sites_not_other(cfg):
    rows = [{"collect_ts": "2020-01-05", "site_grouped": "blood"}, {"collect_ts": "2020-01-06", "site_grouped": "blood"},
            {"collect_ts": "2020-01-07", "site_grouped": "other"}, {"collect_ts": "2020-01-08", "site_grouped": "urine"}]
    out = agg(cfg, *rows)
    assert out.loc["e1", "n_sites"] == 2 and out.loc["e1", "n_samples"] == 4


def test_unknown_status_and_other_site_samples_count_for_span_and_samples(cfg):
    rows = [{"collect_ts": "2020-01-05", "esbl_status": 1}, {"collect_ts": "2020-01-20", "esbl_status": None, "site_grouped": "other"}]
    out = agg(cfg, *rows)
    assert out.loc["e1", "n_samples"] == 2 and out.loc["e1", "culture_span_days"] == 15


def test_times_and_whole_day_intervals(cfg):
    out = agg(cfg, {"collect_ts": "2020-01-10 12:00"}, {"collect_ts": "2020-01-05 09:00"})
    row = out.loc["e1"]
    assert row["first_culture_dt"] == pd.Timestamp("2020-01-05 09:00")
    assert row["last_culture_dt"] == pd.Timestamp("2020-01-10 12:00")
    assert row["culture_span_days"] == 5
    assert row["days_from_admission_to_first_culture"] == 4
    assert row["length_of_stay_days"] == 31


def test_surgery_variables_come_from_the_first_sample(cfg):
    rows = [{"collect_ts": "2020-01-20", "procedure_desc": "later", "tfc_group": "vascular", "n_surgeries_before": 2,
             "days_from_surgery_to_sample": 9.0},
            {"collect_ts": "2020-01-05", "procedure_desc": "first", "tfc_group": "ortho_plastics", "n_surgeries_before": 1,
             "days_from_surgery_to_sample": 3.0}]
    row = agg(cfg, *rows).loc["e1"]
    assert (row["procedure_desc"], row["tfc"], row["n_surgeries"], row["min_days_from_surgery_to_infection"]) == \
        ("first", "ortho_plastics", 1, 3.0)
    assert row["surgery_length"] == pytest.approx(2.5)


def test_spell_columns_are_renamed_and_taken_once(cfg):
    row = agg(cfg, {"collect_ts": "2020-01-05"}, {"collect_ts": "2020-01-06"}).loc["e1"]
    assert row["age_at_admission"] == 60 and row["imd_decile"] == 5 and row["subject"] == "p1"
    assert row["admission_date"] == pd.Timestamp("2020-01-01 08:00")


def test_spell_value_differing_within_an_episode_raises(cfg):
    with pytest.raises(ValueError, match=r"aggregate_episodes.*age_at_admission_n"):
        aggregate_episodes(make({"collect_ts": "2020-01-05"}, {"collect_ts": "2020-01-06", "age_at_admission_n": 61}), cfg)


def test_two_organisms_in_one_episode_raise(cfg):
    with pytest.raises(ValueError, match="organism"):
        aggregate_episodes(make({"collect_ts": "2020-01-05"},
                                {"collect_ts": "2020-01-06", "organism_bug": "proteus mirabilis"}), cfg)


def test_input_order_does_not_matter(cfg):
    rows = [{"collect_ts": "2020-01-20"}, {"collect_ts": "2020-01-05"}, {"collect_ts": "2020-01-09", "infection_id": "e2"}]
    a = aggregate_episodes(make(*rows), cfg)
    b = aggregate_episodes(make(*rows[::-1]), cfg)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_dtype=False)


def test_unknown_site_label_raises(cfg):
    with pytest.raises(ValueError, match="neck"):
        aggregate_episodes(make({"collect_ts": "2020-01-05", "site_grouped": "neck"}), cfg)


def test_missing_collection_time_raises(cfg):
    df = make({"collect_ts": "2020-01-05"})
    df.loc[0, "collect_ts"] = pd.NaT
    with pytest.raises(ValueError, match=r"aggregate_episodes.*1"):
        aggregate_episodes(df, cfg)


def test_missing_column_raises(cfg):
    with pytest.raises(ValueError, match="tfc_group"):
        aggregate_episodes(make({"collect_ts": "2020-01-05"}).drop(columns="tfc_group"), cfg)


def test_duplicate_samples_raise(cfg):
    df = make({"collect_ts": "2020-01-05"}, {"collect_ts": "2020-01-06"})
    df["lab_test_id"] = 0
    with pytest.raises(ValueError, match="duplicate sample"):
        aggregate_episodes(df, cfg)


def test_episodes_without_status_are_excluded(cfg):
    eps = aggregate_episodes(make({"collect_ts": "2020-01-05", "esbl_status": None},
                                  {"collect_ts": "2020-01-06", "infection_id": "e2", "esbl_status": 0}), cfg)
    assert exclude_episodes_without_status(eps, cfg)["infection_id"].tolist() == ["e2"]


def test_other_site_episodes_are_excluded(cfg):
    eps = aggregate_episodes(make({"collect_ts": "2020-01-05", "site_grouped": "other"},
                                  {"collect_ts": "2020-01-06", "infection_id": "e2", "site_grouped": "blood"}), cfg)
    assert exclude_other_site_episodes(eps, cfg)["infection_id"].tolist() == ["e2"]


def test_surgery_length_comes_from_first_sample(cfg):
    out = agg(cfg, {"collect_ts": "2020-01-05", "surgery_length_hours": 2.5}, {"collect_ts": "2020-01-09", "surgery_length_hours": 9.0})
    assert out.loc["e1", "surgery_length"] == 2.5
