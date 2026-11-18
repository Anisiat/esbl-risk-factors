"""Tests for screening exclusion and sample eligibility (``esbl_rf.eligibility``).

Eligible sample (analysis_plan section 2, step 5): non-screening; collected after
a surgery in the spell; >=48 h after admission; <= 30 days after the attributed
(most recent preceding) surgery. Samples collected after discharge are eligible
if they meet these (decided 2026-11-16), so there is no discharge condition.
"""
from __future__ import annotations

import pandas as pd

from esbl_rf.eligibility import eligibility_flags, exclude_screening_samples, filter_eligible_samples

import pytest

ADM = pd.Timestamp("2020-01-01 08:00")
SURG = pd.Timestamp("2020-01-02 08:00")


def sample(collect, surgery=SURG, admission=ADM, **extra):
    row = {"subject": "p1", "spell_identifier": "a", "lab_test_id": 1, "organism_bug": "escherichia coli",
           "collect_ts": pd.Timestamp(collect), "admission_ts": admission, "surgery_anchor_ts": surgery,
           "culture_code": "ucul", "site": "urine"}
    row.update(extra)
    return row


def frame(*rows):
    df = pd.DataFrame(list(rows))
    df["lab_test_id"] = range(len(df))
    return df


def n_kept(cfg, *rows):
    return len(filter_eligible_samples(frame(*rows), cfg))


def test_exactly_48_hours_after_admission_is_eligible(cfg):
    assert n_kept(cfg, sample(ADM + pd.Timedelta(hours=48))) == 1


def test_47_hours_59_minutes_after_admission_is_not_eligible(cfg):
    assert n_kept(cfg, sample(ADM + pd.Timedelta(hours=47, minutes=59))) == 0


def test_exactly_30_days_after_surgery_is_eligible(cfg):
    assert n_kept(cfg, sample(SURG + pd.Timedelta(days=30))) == 1


def test_30_days_and_a_minute_after_surgery_is_not_eligible(cfg):
    assert n_kept(cfg, sample(SURG + pd.Timedelta(days=30, minutes=1))) == 0


def test_sample_without_a_preceding_surgery_is_not_eligible(cfg):
    assert n_kept(cfg, sample("2020-01-10", surgery=pd.NaT)) == 0


def test_sample_after_discharge_is_eligible_within_the_window(cfg):
    """No discharge condition: CHANGE vs the manuscript (decided 2026-11-16)."""
    df = frame(sample("2020-01-20")).assign(discharge_ts=pd.Timestamp("2020-01-05"))
    assert len(filter_eligible_samples(df, cfg)) == 1


def test_window_comes_from_config(cfg):
    cfg["sample_eligibility"]["max_days_after_surgery"] = 7
    assert n_kept(cfg, sample(SURG + pd.Timedelta(days=8))) == 0
    assert n_kept(cfg, sample(SURG + pd.Timedelta(days=7))) == 1


def test_flags_are_one_column_per_rule(cfg):
    flags = eligibility_flags(frame(sample("2020-01-10", surgery=pd.NaT)), cfg)
    assert list(flags.columns) == ["postoperative", "at_least_min_hours_after_admission", "within_max_days_after_surgery"]
    assert flags.iloc[0].tolist() == [False, True, False]


def test_filter_keeps_order_and_index(cfg):
    df = frame(sample("2020-01-10"), sample("2020-01-01 09:00"), sample("2020-01-12"))
    df.index = [5, 6, 7]
    assert list(filter_eligible_samples(df, cfg).index) == [5, 7]


def test_missing_admission_time_raises(cfg):
    with pytest.raises(ValueError, match=r"eligibility_flags.*admission_ts.*1"):
        eligibility_flags(frame(sample("2020-01-10", admission=pd.NaT)), cfg)


def test_screening_by_culture_code_is_excluded_case_insensitively(cfg):
    df = frame(sample("2020-01-10", culture_code="CROCUL"), sample("2020-01-10", culture_code="ucul"))
    assert exclude_screening_samples(df, cfg)["lab_test_id"].tolist() == [1]


def test_rectal_site_is_excluded(cfg):
    df = frame(sample("2020-01-10", site=" Rectal Swab "), sample("2020-01-10", site="urine"))
    assert exclude_screening_samples(df, cfg)["lab_test_id"].tolist() == [1]


def test_missing_code_and_site_are_not_screening(cfg):
    df = frame(sample("2020-01-10", culture_code=None, site=None))
    assert len(exclude_screening_samples(df, cfg)) == 1
