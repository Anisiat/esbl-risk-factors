"""Shared fixtures for the test suite."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from esbl_rf.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg() -> dict:
    """The real ``config.yaml``, so tests use the same column names and thresholds."""
    return load_config(REPO_ROOT / "config.yaml")


@pytest.fixture
def make_samples(cfg):
    """Build a sample-level table (one row per spell x lab test x organism).

    Each row is ``(spell, organism, collect_time)``; ``collect_time`` is
    anything ``pd.Timestamp`` accepts, or ``None`` for a missing time.
    """
    cols = cfg["columns"]

    def _make(rows: list[tuple]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=[cols["spell"], cols["organism"], cols["collect"]])
        df[cols["collect"]] = pd.to_datetime(df[cols["collect"]])
        df[cols["subject"]] = "p1"
        return df

    return _make
