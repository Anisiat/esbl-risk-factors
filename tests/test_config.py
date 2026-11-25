"""config.yaml copies of SQL parameters must match sql/pipeline/00_params.sql."""
import re
from pathlib import Path

import pandas as pd

from esbl_rf.export import definitions_table

PARAMS = Path(__file__).resolve().parents[1] / "sql" / "pipeline" / "00_params.sql"


def sql_params() -> dict[str, str]:
    """``value AS name`` pairs from the AT_PARAMS select (quotes and casts stripped)."""
    found = re.findall(r"^\s*('?[\w\-.]+'?)(?:::\w+)?\s+AS\s+(\w+)", PARAMS.read_text(), flags=re.M | re.I)
    return {name.lower(): value.strip("'") for value, name in found}


def test_config_matches_sql_params(cfg):
    p = sql_params()
    assert p["study_start"] == cfg["study"]["study_start"]
    assert p["study_end"] == cfg["study"]["study_end"]
    assert int(p["min_age"]) == cfg["study"]["min_age"]
    assert int(p["lookback_days"]) == cfg["exposures"]["lookback_days"]
    assert int(p["max_days_after_surgery"]) == cfg["sample_eligibility"]["max_days_after_surgery"]
    assert int(p["prophylaxis_hours_before_start"]) == cfg["prophylaxis"]["hours_before_start"]
    assert int(p["prophylaxis_hours_after_stop"]) == cfg["prophylaxis"]["hours_after_stop"]


def test_definitions_table_is_complete_text(cfg):
    table, notes = definitions_table(cfg)
    assert list(table.columns) == ["Domain", "Item", "Definition"] and notes
    assert table["Definition"].str.len().gt(0).all() and not table["Item"].duplicated().any()
    defs = table.set_index("Item")["Definition"]
    assert "11; 12; 13" in defs["Elective admission"]
    assert "\\b" not in " ".join(defs) and "wound, pus" in defs["Specimen site: Wound"]
    assert "Antibiotics in previous 90 days" in defs["Variable selection"]
