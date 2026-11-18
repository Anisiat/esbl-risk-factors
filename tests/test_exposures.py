"""Tests for esbl_rf.exposures."""
import pandas as pd
import pytest

from esbl_rf.exposures import flag_prior_esbl, flag_prior_inpatient


def episodes(rows):
    """rows: (infection_id, subject, spell, admission)"""
    df = pd.DataFrame(rows, columns=["infection_id", "subject", "spell_identifier", "admission_date"])
    df["admission_date"] = pd.to_datetime(df["admission_date"])
    return df


def prior(rows):
    """rows: (subject, index_spell, index_admission, prior_spell, prior_admission, prior_discharge)"""
    df = pd.DataFrame(rows, columns=["subject", "spell_identifier", "index_admission_d", "prior_spell_identifier",
                                      "prior_admission_d", "prior_discharge_d"])
    for c in ("index_admission_d", "prior_admission_d", "prior_discharge_d"):
        df[c] = pd.to_datetime(df[c])
    return df


EPS = [("e1", "p1", "s1", "2024-06-01 08:00"), ("e2", "p1", "s1", "2024-06-01 08:00"), ("e3", "p2", "s9", "2024-06-01 08:00")]


def test_inpatient_flag_is_per_spell(cfg):
    p = prior([("p1", "s1", "2024-06-01", "s0", "2024-04-01", "2024-04-05")])
    out = flag_prior_inpatient(episodes(EPS), p, cfg)
    assert out.set_index("infection_id")["inpatient_exposure_90d"].tolist() == [1, 1, 0]
    assert str(out["inpatient_exposure_90d"].dtype) == "Int64"
    assert len(out) == 3


def test_inpatient_empty_table_gives_zeros(cfg):
    out = flag_prior_inpatient(episodes(EPS), prior([]), cfg)
    assert out["inpatient_exposure_90d"].tolist() == [0, 0, 0]


def test_inpatient_rows_outside_window_raise(cfg):
    p = prior([("p1", "s1", "2024-06-01", "s0", "2023-01-01", "2023-01-05")])  # ended > 90 d before
    with pytest.raises(ValueError, match="window"):
        flag_prior_inpatient(episodes(EPS), p, cfg)


def test_inpatient_prior_after_admission_raises(cfg):
    p = prior([("p1", "s1", "2024-06-01", "s0", "2024-06-02", "2024-06-05")])
    with pytest.raises(ValueError, match="window"):
        flag_prior_inpatient(episodes(EPS), p, cfg)


def test_inpatient_same_spell_raises(cfg):
    p = prior([("p1", "s1", "2024-06-01", "s1", "2024-04-01", "2024-04-05")])
    with pytest.raises(ValueError, match="index spell"):
        flag_prior_inpatient(episodes(EPS), p, cfg)


def samples(rows):
    """rows: (subject, lab_test_id, collect_ts, esbl_status)"""
    df = pd.DataFrame(rows, columns=["subject", "lab_test_id", "collect_ts", "esbl_status"])
    df["collect_ts"] = pd.to_datetime(df["collect_ts"])
    df["esbl_status"] = df["esbl_status"].astype("Int64")
    df["organism_bug"] = "escherichia coli"
    return df


def test_prior_esbl_only_positive_before_admission(cfg):
    s = samples([
        ("p1", 1, "2024-01-01", 1),      # positive before -> counts
        ("p2", 2, "2024-01-01", 0),      # negative
        ("p2", 3, "2024-01-02", pd.NA),  # unknown
        ("p3", 4, "2024-07-01", 1),      # positive but after admission
    ])
    eps = episodes(EPS + [("e4", "p3", "s5", "2024-06-01 08:00")])
    out = flag_prior_esbl(eps, s, cfg).set_index("infection_id")["prior_positive_esbl"]
    assert out.to_dict() == {"e1": 1, "e2": 1, "e3": 0, "e4": 0}


def test_prior_esbl_strictly_before_admission_time(cfg):
    s = samples([("p1", 1, "2024-06-01 08:00", 1)])
    out = flag_prior_esbl(episodes(EPS[:1]), s, cfg)
    assert out["prior_positive_esbl"].tolist() == [0]


def test_prior_esbl_lookback(cfg):
    cfg["exposures"]["prior_esbl_lookback_days"] = 30
    s = samples([("p1", 1, "2024-01-01", 1), ("p2", 2, "2024-05-10", 1)])
    out = flag_prior_esbl(episodes(EPS), s, cfg).set_index("infection_id")["prior_positive_esbl"]
    assert out.to_dict() == {"e1": 0, "e2": 0, "e3": 1}


def test_prior_esbl_missing_columns_raise(cfg):
    with pytest.raises(ValueError, match="missing column"):
        flag_prior_esbl(episodes(EPS), samples([("p1", 1, "2024-01-01", 1)]).drop(columns="esbl_status"), cfg)


def test_prior_esbl_missing_collection_time_ignored(cfg):
    s = samples([("p1", 1, None, 1)])
    assert flag_prior_esbl(episodes(EPS), s, cfg)["prior_positive_esbl"].tolist() == [0, 0, 0]
