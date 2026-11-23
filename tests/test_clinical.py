"""Tests for temperature/CRP and in-hospital mortality (``esbl_rf.clinical``)."""
import pandas as pd
import pytest

from esbl_rf.clinical import add_nearest_measurement, flag_in_hospital_death

CULTURE = pd.Timestamp("2020-01-05 12:00")


def episodes():
    return pd.DataFrame({"infection_id": ["a", "b"], "subject": ["p1", "p2"], "spell_identifier": ["s1", "s2"],
                         "first_culture_dt": CULTURE, "surgery_start_dt": pd.Timestamp("2020-01-03 09:00")})


def temps(rows):
    df = pd.DataFrame(rows, columns=["subject", "spell_identifier", "hours", "temperature"])
    df["temp_performed_ts"] = CULTURE + pd.to_timedelta(df.pop("hours"), unit="h")
    return df


def test_nearest_value_within_window_after_surgery(cfg):
    out = add_nearest_measurement(episodes(), temps([
        ("p1", "s1", -10, "38.0"),
        ("p1", "s1", 3, "39.1"),       # nearest
        ("p1", "s1", 1, "101.3"),      # nearer but implausible (Fahrenheit): dropped
        ("p2", "s2", -60, "38.5"),     # before surgery and outside 48 h
        ("p2", "s2", 50, "38.2"),      # outside 48 h
    ]), "temperature", cfg).set_index("infection_id")
    assert out.loc["a", "temperature"] == pytest.approx(39.1)
    assert pd.isna(out.loc["b", "temperature"])


def test_tie_goes_to_earlier_measurement(cfg):
    out = add_nearest_measurement(episodes(), temps([("p1", "s1", 2, "39.0"), ("p1", "s1", -2, "38.0")]),
                                  "temperature", cfg)
    assert out.loc[0, "temperature"] == pytest.approx(38.0)


def test_other_spell_not_used(cfg):
    out = add_nearest_measurement(episodes(), temps([("p1", "other", 1, "38.0")]), "temperature", cfg)
    assert out["temperature"].isna().all()


def test_in_hospital_death_from_discharge_method(cfg):
    eps = pd.DataFrame({"discharge_method": [4, 1, 9, None, "04"]})
    out = flag_in_hospital_death(eps, cfg)["in_hosp_mortality"]
    assert out.iloc[0] == 1 and out.iloc[1] == 0 and out.iloc[4] == 1
    assert out.iloc[2:4].isna().all()
