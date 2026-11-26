"""Tests for specimen-site grouping (``esbl_rf.sites``)."""
from __future__ import annotations

import pandas as pd
import pytest

from esbl_rf.sites import group_specimen_site


def group(cfg, site, culture_type=None):
    df = pd.DataFrame({"site": [site], "culture_type": [culture_type]})
    return group_specimen_site(df, cfg)["site_grouped"].iloc[0]


@pytest.mark.parametrize("site, culture_type, expected", [
    ("Blood", None, "blood"),
    ("peripheral blood", "Blood Culture", "blood"),
    ("MSU urine", None, "urine"),
    ("catheter tip", None, "tips_devices"),
    ("Bronchoalveolar lavage", None, "sputum"),
    ("wound swab", None, "wound"),
    ("pus", None, "wound"),
    ("pleural fluid", None, "drain"),
    ("tissue/biopsy", None, "tissue/biopsy"),
    ("tissue-biopsy", None, "tissue/biopsy"),
    ("high vaginal swab", "High vaginal culture", "genital"),
    ("vagina", None, "genital"),
    ("HVS", None, "genital"),
    ("low vaginal swab", None, "genital"),
    ("vaginal wound swab", None, "wound"),
    ("throat swab", None, "other"),
    (None, None, "other"),
    ("bloodstream", None, "other"),
])
def test_site_is_grouped(cfg, site, culture_type, expected):
    assert group(cfg, site, culture_type) == expected


def test_culture_type_is_used_when_site_is_blank(cfg):
    assert group(cfg, None, "Blood culture") == "blood"


def test_earlier_group_wins_when_several_match(cfg):
    """urine is listed before tips_devices, so 'urine catheter' is urine."""
    assert group(cfg, "urine catheter") == "urine"


def test_extra_whitespace_and_case_are_ignored(cfg):
    assert group(cfg, "  WOUND   Swab ") == "wound"


def test_patterns_come_from_config(cfg):
    cfg["sites"]["groups"].append({"label": "throat", "pattern": r"\bthroat\b"})
    cfg["sites"]["hierarchy"].insert(-1, "throat")
    assert group(cfg, "throat swab") == "throat"


def test_input_is_not_modified_and_index_kept(cfg):
    df = pd.DataFrame({"site": ["blood", "urine"], "culture_type": [None, None]}, index=[4, 9])
    before = df.copy()
    out = group_specimen_site(df, cfg)
    pd.testing.assert_frame_equal(df, before)
    assert list(out.index) == [4, 9]


def test_missing_column_raises(cfg):
    with pytest.raises(ValueError, match="culture_type"):
        group_specimen_site(pd.DataFrame({"site": ["blood"]}), cfg)
