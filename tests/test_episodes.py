"""Tests for infection-episode assignment (``esbl_rf.episodes``).

Rule (analysis_plan section 4): within a spell, samples of the same organism
belong to one episode until the gap since the PREVIOUS sample exceeds
``episodes.gap_days`` (14). CHANGE vs baseline: the gap is compared exactly
(Timedelta), not as a floored integer number of days, so 14 d 23 h is a new
episode (the baseline treated it as the same one).
"""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.episodes import assign_infection_episodes

EC, KP = "escherichia coli", "klebsiella pneumoniae"


def _n_episodes(out: pd.DataFrame) -> int:
    return out["infection_id"].nunique()


def test_single_sample_is_one_episode(cfg, make_samples):
    out = assign_infection_episodes(make_samples([("s1", EC, "2020-01-01")]), cfg)
    assert _n_episodes(out) == 1


def test_samples_three_days_apart_share_an_episode(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", EC, "2020-01-04")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 1


def test_gap_of_exactly_14_days_is_same_episode(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01 08:00"), ("s1", EC, "2020-01-15 08:00")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 1


def test_gap_of_14_days_23_hours_is_a_new_episode(cfg, make_samples):
    """CHANGE vs baseline: the floored day count (14) would have kept these together."""
    df = make_samples([("s1", EC, "2020-01-01 08:00"), ("s1", EC, "2020-01-16 07:00")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 2


def test_gap_of_15_days_is_a_new_episode(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", EC, "2020-01-16")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 2


def test_chain_of_samples_is_one_episode(cfg, make_samples):
    """Day 0, 10, 20: each gap is 10 d (from the previous sample), so one episode
    even though day 20 is more than 14 d after the first sample."""
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", EC, "2020-01-11"), ("s1", EC, "2020-01-21")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 1


def test_different_organisms_are_different_episodes(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", KP, "2020-01-02")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 2


def test_different_spells_are_different_episodes(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01"), ("s2", EC, "2020-01-02")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 2


def test_unsorted_input_keeps_rows_order_and_index(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-21"), ("s1", EC, "2020-01-01"), ("s1", EC, "2020-01-11")])
    df.index = [10, 11, 12]
    out = assign_infection_episodes(df, cfg)
    assert list(out.index) == [10, 11, 12]
    assert len(out) == 3
    assert _n_episodes(out) == 1
    assert list(out[cfg["columns"]["collect"]]) == list(df[cfg["columns"]["collect"]])


def test_input_is_not_modified(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01")])
    before = df.copy()
    assign_infection_episodes(df, cfg)
    pd.testing.assert_frame_equal(df, before)


def test_missing_collection_time_raises_with_count(cfg, make_samples):
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", EC, None), ("s1", EC, None)])
    with pytest.raises(ValueError, match=r"assign_infection_episodes.*2"):
        assign_infection_episodes(df, cfg)


def test_gap_days_comes_from_config(cfg, make_samples):
    cfg["episodes"]["gap_days"] = 7
    df = make_samples([("s1", EC, "2020-01-01"), ("s1", EC, "2020-01-10")])
    assert _n_episodes(assign_infection_episodes(df, cfg)) == 2
