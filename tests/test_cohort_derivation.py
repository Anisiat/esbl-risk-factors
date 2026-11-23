"""Tests for the cohort derivation log (``esbl_rf.cohort_derivation``)."""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.cohort_derivation import build_flowchart, derivation_table, record_step, reset_derivation


@pytest.fixture
def cfg_tmp(cfg, tmp_path):
    """Config whose checkpoints go to a temporary folder, never to data/interim."""
    cfg["paths"]["interim_dir"] = tmp_path
    return cfg


def samples(rows: list[tuple]) -> pd.DataFrame:
    """Rows of (subject, lab_test_id, organism_bug)."""
    return pd.DataFrame(rows, columns=["subject", "lab_test_id", "organism_bug"])


EC = "escherichia coli"
THREE = samples([("p1", 1, EC), ("p1", 2, EC), ("p2", 3, EC)])


def test_first_step_counts_patients_and_samples(cfg_tmp):
    record_step(cfg_tmp, "start", THREE)
    row = derivation_table(cfg_tmp).iloc[0]
    assert (row["step"], row["n_patients"], row["n_samples"]) == ("start", 2, 3)
    assert pd.isna(row["n_episodes"])


def test_episodes_counted_when_column_given(cfg_tmp):
    df = THREE.assign(infection_id=["a", "a", "b"])
    record_step(cfg_tmp, "episodes", df, episode_col="infection_id")
    assert derivation_table(cfg_tmp).iloc[0]["n_episodes"] == 2


def test_excluded_counts_are_differences_between_steps(cfg_tmp):
    record_step(cfg_tmp, "start", THREE)
    record_step(cfg_tmp, "after filter", THREE.iloc[:2])
    out = derivation_table(cfg_tmp)
    assert pd.isna(out.loc[0, "samples_excluded"])
    assert out.loc[1, "samples_excluded"] == 1 and out.loc[1, "patients_excluded"] == 1


def test_recording_a_step_again_replaces_it_in_place(cfg_tmp):
    record_step(cfg_tmp, "a", THREE)
    record_step(cfg_tmp, "b", THREE.iloc[:2])
    record_step(cfg_tmp, "a", THREE)
    out = derivation_table(cfg_tmp)
    assert list(out["step"]) == ["a", "b"]


def test_log_persists_between_calls(cfg_tmp):
    record_step(cfg_tmp, "a", THREE)
    assert len(derivation_table(cfg_tmp)) == 1
    record_step(cfg_tmp, "b", THREE.iloc[:2])
    assert len(derivation_table(cfg_tmp)) == 2


def test_episode_level_table_without_sample_key_has_no_sample_count(cfg_tmp):
    record_step(cfg_tmp, "samples", THREE)
    episodes = pd.DataFrame({"subject": ["p1", "p2"], "infection_id": ["a", "b"]})
    record_step(cfg_tmp, "aggregated", episodes, episode_col="infection_id")
    row = derivation_table(cfg_tmp).iloc[1]
    assert pd.isna(row["n_samples"]) and row["n_episodes"] == 2


def test_patient_count_rising_raises(cfg_tmp):
    record_step(cfg_tmp, "a", THREE.iloc[:1])
    with pytest.raises(ValueError, match=r"record_step\['b'\].*more than"):
        record_step(cfg_tmp, "b", THREE)


def test_missing_subject_column_raises(cfg_tmp):
    with pytest.raises(ValueError, match="subject"):
        record_step(cfg_tmp, "a", THREE.drop(columns="subject"))


def test_reset_clears_the_log(cfg_tmp):
    record_step(cfg_tmp, "a", THREE)
    reset_derivation(cfg_tmp)
    assert len(derivation_table(cfg_tmp)) == 0


def test_admissions_and_esbl_positive_counted(cfg_tmp):
    episodes = pd.DataFrame({"subject": ["p1", "p1", "p2"], "spell_identifier": ["s1", "s2", "s3"],
                             "infection_id": ["a", "b", "c"], "esbl_status": [1, 0, 1]})
    record_step(cfg_tmp, "episodes", episodes, episode_col="infection_id", esbl_col="esbl_status")
    row = derivation_table(cfg_tmp).iloc[0]
    assert (row["n_admissions"], row["n_esbl_pos"]) == (3, 2)


def test_esbl_count_on_sample_level_rows_raises(cfg_tmp):
    df = THREE.assign(infection_id=["a", "a", "b"], esbl_status=[1, 1, 0])
    with pytest.raises(ValueError, match="one row per episode"):
        record_step(cfg_tmp, "x", df, episode_col="infection_id", esbl_col="esbl_status")


# --- build_flowchart ------------------------------------------------------

def sql_flow(adm3: int = 3, pat3: int = 2) -> pd.DataFrame:
    return pd.DataFrame({
        "step": [1, 2, 3, 31], "description": ["all", "elective", "culture", "of which"],
        "n_admissions": [10, 8, adm3, 1], "n_patients": [9, 7, pat3, 1],
        "n_surgery_events": [12, 9, None, None], "n_cultures": [None, None, 4, 2]})


def py_log(cfg_tmp) -> pd.DataFrame:
    df = THREE.assign(spell_identifier=["s1", "s2", "s3"])
    record_step(cfg_tmp, "samples", df)
    record_step(cfg_tmp, "eligible", df.iloc[:2])
    return derivation_table(cfg_tmp)


def test_flowchart_joins_sql_then_python(cfg_tmp):
    out = build_flowchart(sql_flow(), py_log(cfg_tmp), cfg_tmp)
    assert list(out["source"]) == ["sql"] * 4 + ["python"] * 2
    assert list(out["order"]) == [1, 2, 3, 4, 5, 6]
    assert out.loc[1, "patients_excluded"] == 2              # 9 -> 7
    assert pd.isna(out.loc[3, "patients_excluded"])          # informational row: outside the chain
    assert out.loc[4, "patients_excluded"] == 0              # SQL step 3 -> first Python step
    assert pd.isna(out.loc[4, "samples_excluded"])           # different grain, not an exclusion
    assert out.loc[5, "patients_excluded"] == 1


def test_flowchart_mismatched_builds_raise(cfg_tmp):
    with pytest.raises(ValueError, match="same SQL build"):
        build_flowchart(sql_flow(adm3=5), py_log(cfg_tmp), cfg_tmp)


def test_flowchart_rising_patients_raise(cfg_tmp):
    flow = sql_flow()
    flow.loc[1, "n_patients"] = 1                            # step 2 below step 3
    with pytest.raises(ValueError, match="patient count rises"):
        build_flowchart(flow, py_log(cfg_tmp), cfg_tmp)
