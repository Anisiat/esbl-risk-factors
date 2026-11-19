"""Tests for esbl_rf.comorbidity (Charlson flags and CCI via comorbidicare).

Evidence must be dated strictly before admission. Codes used (from the
comorbidicare mapping files): ICD-10 I21 = myocardial infarction (weight 1),
SNOMED 95443002 = peripheral vascular disease (weight 1), gliclazide = diabetes
without complication (by prescription).
"""
import pandas as pd
import pytest

from esbl_rf.comorbidity import add_comorbidities

ADM = pd.Timestamp("2024-06-01 08:00")


def episodes(*rows):
    """rows: (infection_id, subject, spell)"""
    df = pd.DataFrame(rows, columns=["infection_id", "subject", "spell_identifier"])
    df["admission_date"], df["discharge_date"] = ADM, ADM + pd.Timedelta(days=7)
    return df


def diagnoses(*rows):
    """rows: (subject, spell, icd, date)"""
    df = pd.DataFrame(rows, columns=["subject", "spell_identifier", "diagnosis_code_icd", "diagnosis_date"])
    df["diagnosis_code_snomed"] = None
    df["diagnosis_date"] = pd.to_datetime(df["diagnosis_date"], format="mixed")
    return df


EMPTY_RX = pd.DataFrame(columns=["subject", "medication_name_short", "order_dt_tm"])
EMPTY_PROBLEMS = pd.DataFrame(columns=["subject", "problem_code", "problem_dt_tm"])
EPS = episodes(("e1", "p1", "s1"), ("e2", "p1", "s1"), ("e3", "p2", "s2"))


def run(cfg, dx, rx=EMPTY_RX, problems=EMPTY_PROBLEMS, eps=EPS):
    return add_comorbidities(eps, dx, rx, problems, cfg).set_index("infection_id")


def test_flag_score_and_group_from_earlier_diagnosis(cfg):
    out = run(cfg, diagnoses(("p1", "s1", "I21", "2023-01-01")))
    assert out.loc["e1", "myocardial_infarction"] == 1 and out.loc["e3", "myocardial_infarction"] == 0
    assert out.loc["e1", "cci_score"] == 1 and out.loc["e1", "cci_group"] == "1-2"
    assert out.loc["e3", "cci_score"] == 0 and out.loc["e3", "cci_group"] == "0"


def test_episodes_in_same_spell_share_values(cfg):
    out = run(cfg, diagnoses(("p1", "s1", "I21", "2023-01-01")))
    assert out.loc["e1", "cci_score"] == out.loc["e2", "cci_score"]


def test_evidence_at_or_after_admission_is_ignored(cfg):
    out = run(cfg, diagnoses(("p1", "s1", "I21", "2024-06-01 08:00"), ("p2", "s2", "I21", "2024-07-01")))
    assert out["myocardial_infarction"].sum() == 0


def test_problems_and_prescriptions_count(cfg):
    problems = pd.DataFrame({"subject": ["p1"], "problem_code": ["95443002"], "problem_dt_tm": [pd.Timestamp("2023-01-01")]})
    rx = pd.DataFrame({"subject": ["p2"], "medication_name_short": ["gliclazide"], "order_dt_tm": [pd.Timestamp("2023-01-01")]})
    out = run(cfg, diagnoses(("p9", "s9", "I21", "2023-01-01")), rx=rx, problems=problems)
    assert out.loc["e1", "peripheral_vascular_disease"] == 1
    assert out.loc["e3", "diabetes_without_complication"] == 1


def test_all_flags_and_types(cfg):
    out = run(cfg, diagnoses(("p1", "s1", "I21", "2023-01-01")))
    for c in cfg["table1"]["supplementary_conditions"]:
        assert c in out.columns and set(out[c].unique()) <= {0, 1}
    assert str(out["cci_group"].dtype) == "string"


def test_keeps_rows_and_order(cfg):
    out = add_comorbidities(EPS, diagnoses(("p1", "s1", "I21", "2023-01-01")), EMPTY_RX, EMPTY_PROBLEMS, cfg)
    assert out["infection_id"].tolist() == ["e1", "e2", "e3"]


def test_no_evidence_at_all_raises(cfg):
    empty_dx = diagnoses().iloc[0:0]
    with pytest.raises(ValueError):   # raised by comorbidicare
        add_comorbidities(EPS, empty_dx, EMPTY_RX, EMPTY_PROBLEMS, cfg)


def test_existing_column_raises(cfg):
    with pytest.raises(ValueError, match="already has"):
        add_comorbidities(EPS.assign(cci_score=0), diagnoses(("p1", "s1", "I21", "2023-01-01")), EMPTY_RX, EMPTY_PROBLEMS, cfg)


def test_conflicting_spell_dates_raise(cfg):
    eps = EPS.copy()
    eps.loc[1, "admission_date"] += pd.Timedelta(days=1)
    with pytest.raises(ValueError):   # raised by comorbidicare; wording is its own
        add_comorbidities(eps, diagnoses(("p1", "s1", "I21", "2023-01-01")), EMPTY_RX, EMPTY_PROBLEMS, cfg)


def test_missing_episode_column_raises(cfg):
    with pytest.raises(KeyError, match="discharge_date"):
        add_comorbidities(EPS.drop(columns="discharge_date"), diagnoses(("p1", "s1", "I21", "2023-01-01")), EMPTY_RX, EMPTY_PROBLEMS, cfg)
