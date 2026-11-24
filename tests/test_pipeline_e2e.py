"""End to end: synthetic CSVs in, every checkpoint out (no real data)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

from esbl_rf.cohort_derivation import derivation_table, reset_derivation
from esbl_rf.steps import STEPS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))
from generate import generate  # noqa: E402


@pytest.fixture
def run_cfg(cfg, tmp_path):
    """The real config, pointed at a temporary folder holding synthetic raw CSVs."""
    raw = tmp_path / "raw"
    generate(raw, n_patients=120, seed=3)
    cfg["paths"]["raw_dir"], cfg["paths"]["interim_dir"] = raw, tmp_path / "interim"
    cfg["raw_csv"] = {name: raw / path.name for name, path in cfg["raw_csv"].items()}
    cfg["demographics"]["ethnicity_min_count"] = 5
    return cfg


def test_full_run_produces_every_checkpoint(run_cfg):
    reset_derivation(run_cfg)
    for run in STEPS.values():
        run(run_cfg)
    interim = run_cfg["paths"]["interim_dir"]
    for name in ("microbiology_labelled", "eligible_samples", "episodes", "episode_exposures", "episode_comorbidity", "episode_clinical", "cohort_derivation", "flowchart",
                 "table1", "table1_supplement"):
        assert (interim / f"{name}.csv").is_file(), name

    com = pd.read_csv(interim / "episode_comorbidity.csv")
    assert len(com) == len(pd.read_csv(interim / "episode_exposures.csv"))
    assert com["cci_score"].ge(0).all() and set(com["cci_group"].unique()) <= {"0", "1-2", ">=3"}
    assert com["myocardial_infarction"].sum() > 0
    assert com["dementia"].sum() > 0, "17-digit SNOMED code did not match: was diagnosis_code_snomed read as text?"

    clin = pd.read_csv(interim / "episode_clinical.csv")
    assert len(clin) == len(com)
    assert clin["past_abx"].isin([0, 1]).all() and 0 < clin["past_abx"].sum() < len(clin)
    groups = set(clin["prophylaxis_group"])
    assert {"cefuroxime +/- metronidazole", "co-amoxiclav-based", "no_prophylaxis"} <= groups
    assert not clin["prophylaxis"].fillna("").str.contains("fluconazole|ciprofloxacin").any()
    temp = clin["temperature"].dropna()
    assert temp.between(30, 45).all() and len(temp) > len(clin) / 2   # Fahrenheit value dropped
    assert clin["crp"].notna().any() and clin["crp"].isna().any()
    assert clin["in_hosp_mortality"].sum() > 0

    out = pd.read_csv(interim / "episode_exposures.csv")
    assert len(out) > 20
    assert out["infection_id"].is_unique
    assert set(out["esbl_status"].unique()) <= {0, 1}
    assert {"ethnicity_desc", "gender", "inpatient_exposure_90d", "prior_positive_esbl"} <= set(out.columns)
    assert out["inpatient_exposure_90d"].sum() > 0 and out["prior_positive_esbl"].sum() > 0
    assert out["site"].ne("other").all()

    steps = derivation_table(run_cfg)
    assert steps["n_patients"].is_monotonic_decreasing
    assert steps["step"].iloc[0] == "samples collected in infection spells"
    assert steps["n_esbl_pos"].iloc[-1] > 0

    t1 = pd.read_csv(interim / "table1.csv")
    assert t1.loc[0, "overall_n"] == len(clin)
    assert (t1["esbl_pos_n"].fillna(0) + t1["non_esbl_n"].fillna(0) == t1["overall_n"].fillna(0)).all()
    listed = {c for cols in run_cfg["table1"]["blocks"].values() for c in cols}
    assert set(t1["variable"]) == listed | {"N"}
    supp = pd.read_csv(interim / "table1_supplement.csv")
    assert set(supp["variable"]) - {"N"} == set(run_cfg["table1"]["supplementary_conditions"])

    flow = pd.read_csv(interim / "flowchart.csv")
    assert list(flow["source"].unique()) == ["sql", "python"]
    assert len(flow) == 4 + len(steps)
    assert flow["n_episodes"].iloc[-1] == len(out)


def test_steps_can_be_rerun_from_checkpoints(run_cfg):
    reset_derivation(run_cfg)
    for run in STEPS.values():
        run(run_cfg)
    first = pd.read_csv(run_cfg["paths"]["interim_dir"] / "episode_exposures.csv")
    STEPS["exposures"](run_cfg)
    pd.testing.assert_frame_equal(first, pd.read_csv(run_cfg["paths"]["interim_dir"] / "episode_exposures.csv"))
