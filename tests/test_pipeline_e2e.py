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
    for name in ("microbiology_labelled", "eligible_samples", "episodes", "episode_exposures", "episode_comorbidity", "cohort_derivation"):
        assert (interim / f"{name}.csv").is_file(), name

    com = pd.read_csv(interim / "episode_comorbidity.csv")
    assert len(com) == len(pd.read_csv(interim / "episode_exposures.csv"))
    assert com["cci_score"].ge(0).all() and set(com["cci_group"].unique()) <= {"0", "1-2", ">=3"}
    assert com["myocardial_infarction"].sum() > 0

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


def test_steps_can_be_rerun_from_checkpoints(run_cfg):
    reset_derivation(run_cfg)
    for run in STEPS.values():
        run(run_cfg)
    first = pd.read_csv(run_cfg["paths"]["interim_dir"] / "episode_exposures.csv")
    STEPS["exposures"](run_cfg)
    pd.testing.assert_frame_equal(first, pd.read_csv(run_cfg["paths"]["interim_dir"] / "episode_exposures.csv"))
