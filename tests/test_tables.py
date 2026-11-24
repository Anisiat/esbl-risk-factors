"""Tests for esbl_rf.tables (Table 1 numbers)."""
import pandas as pd
import pytest

from esbl_rf.tables import level_order, summarise_table


def episodes():
    return pd.DataFrame({
        "esbl_status": [1, 1, 0, 0, 0],
        "age_at_admission": [60, 70, 40, 50, 80],
        "organism_bug": ["klebsiella pneumoniae", "escherichia coli", "klebsiella pneumoniae",
                         "klebsiella pneumoniae", None],
        "past_abx": [1, 0, 0, 1, 0],
    })


BLOCKS = {"Patient": ["age_at_admission"], "Infection": ["organism_bug", "past_abx"]}


def row(table, variable, level=None):
    t = table[table["variable"] == variable]
    return t.iloc[0] if level is None else t[t["level"] == level].iloc[0]


def test_counts_by_group(cfg):
    t = summarise_table(episodes(), BLOCKS, cfg)
    n = row(t, "N")
    assert (n["overall_n"], n["esbl_pos_n"], n["non_esbl_n"]) == (5, 2, 3)
    kp = row(t, "organism_bug", "klebsiella pneumoniae")
    assert (kp["overall_n"], kp["overall_denom"], kp["overall_missing"]) == (3, 4, 1)
    assert (kp["esbl_pos_n"], kp["non_esbl_n"], kp["non_esbl_denom"]) == (1, 2, 2)
    abx = row(t, "past_abx")
    assert abx["kind"] == "binary" and abx["overall_n"] == 2 and abx["esbl_pos_n"] == 1


def test_continuous_median_and_quartiles(cfg):
    age = row(summarise_table(episodes(), BLOCKS, cfg), "age_at_admission")
    assert age["overall_median"] == 60 and age["overall_q1"] == 50 and age["overall_q3"] == 70
    assert age["esbl_pos_median"] == 65


def test_reference_level_comes_first(cfg):
    # E. coli is the reference in config.yaml, though K. pneumoniae is more common
    assert level_order(episodes()["organism_bug"], "organism_bug", cfg)[0] == "escherichia coli"


def test_cci_group_keeps_config_order(cfg):
    assert level_order(pd.Series([">=3", "0", "1-2", "0"]), "cci_group", cfg) == ["0", "1-2", ">=3"]


def test_binary_with_other_values_raises(cfg):
    df = episodes().assign(past_abx=[1, 2, 0, 0, 0])
    with pytest.raises(ValueError, match="binary column 'past_abx'"):
        summarise_table(df, BLOCKS, cfg)


def test_text_in_continuous_column_raises(cfg):
    df = episodes().assign(age_at_admission=["60", "old", 40, 50, 80])
    with pytest.raises(ValueError, match="non-numeric"):
        summarise_table(df, BLOCKS, cfg)


def test_outcome_must_be_zero_or_one(cfg):
    df = episodes().assign(esbl_status=[1, None, 0, 0, 0])
    with pytest.raises(ValueError, match="not 0/1"):
        summarise_table(df, BLOCKS, cfg)


def test_missing_column_raises(cfg):
    with pytest.raises(ValueError, match="missing column"):
        summarise_table(episodes(), {"x": ["crp"]}, cfg)
