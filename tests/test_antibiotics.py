"""Tests for prior antibiotics and perioperative prophylaxis (``esbl_rf.antibiotics``)."""
import pandas as pd
import pytest

from esbl_rf.antibiotics import (add_prophylaxis, choose_prophylaxis, filter_antibiotics, flag_past_abx,
                                 group_prophylaxis)

START = pd.Timestamp("2020-01-02 09:00")


def doses(rows):
    """Rows of (surgery_event_id, agent, minutes from surgery start, route, in_window)."""
    df = pd.DataFrame(rows, columns=["surgery_event_id", "medication_name_short", "minutes", "route", "in_window"])
    df["surgery_start_ts"] = START
    df["administration_ts"] = START + pd.to_timedelta(df.pop("minutes"), unit="min")
    return df


def test_filter_keeps_antibiotics_and_listed_exceptions(cfg):
    admin = pd.DataFrame({"medication_name_short": [" Cefuroxime", "Fluconazole", "Metronidazole", "Aciclovir", None]})
    out = filter_antibiotics(admin, cfg)
    assert list(out["medication_name_short"]) == ["cefuroxime", "metronidazole"]


def test_filter_keeps_co_trimoxazole_and_tetracycline(cfg):
    admin = pd.DataFrame({"medication_name_short": ["Co-trimoxazole", "Sulfamethoxazole", "Tetracycline", "Fluconazole"]})
    out = filter_antibiotics(admin, cfg)
    assert list(out["medication_name_short"]) == ["co-trimoxazole", "sulfamethoxazole", "tetracycline"]


def test_filter_drops_doses_not_given(cfg):
    cfg["antibiotics"]["not_given_statuses"] = ["not given"]
    admin = pd.DataFrame({"medication_name_short": ["cefuroxime", "cefuroxime", "cefuroxime"],
                          "administration_status": ["Given", " Not Given", None]})
    out = filter_antibiotics(admin, cfg)
    assert len(out) == 2 and "Not Given" not in set(out["administration_status"].dropna().str.strip())


def test_status_filter_needs_the_status_column(cfg):
    cfg["antibiotics"]["not_given_statuses"] = ["not given"]
    with pytest.raises(ValueError, match="administration_status"):
        filter_antibiotics(pd.DataFrame({"medication_name_short": ["cefuroxime"]}), cfg)


@pytest.mark.parametrize("classes, label", [
    ({"cefuroxime", "limited_enterobacterales"}, "cefuroxime +/- metronidazole"),
    ({"cefuroxime", "aminoglycoside"}, "cefuroxime +/- metronidazole + aminoglycoside"),
    ({"co_amoxiclav", "aminoglycoside"}, "co-amoxiclav-based"),
    ({"aminoglycoside", "limited_enterobacterales"}, "aminoglycoside-based"),
    ({"limited_enterobacterales"}, "limited_enterobact_activity"),
    ({"limited_enterobacterales", None}, "other"),
    ({"broad_gram_negative", "limited_enterobacterales"}, "other_broad_spectrum_gram_negative_active"),
    ({None}, "other"),
])
def test_group_rules_follow_baseline_order(cfg, classes, label):
    assert group_prophylaxis(classes, cfg) == label


def test_course_is_agents_near_the_anchor_dose(cfg):
    out = choose_prophylaxis(doses([
        ("e1", "Cefuroxime", -30, "IV", 1),
        ("e1", "Metronidazole", -25, "IV", 1),        # within 10 min: same course
        ("e1", "Gentamicin", 60, "IV", 1),            # later: not in the course
        ("e1", "Ciprofloxacin", -30, "Oral", 1),      # not IV
        ("e2", "Meropenem", -600, "IV", 0),           # outside the window
    ]), cfg).set_index("surgery_event_id")
    assert list(out.index) == ["e1"]
    assert out.loc["e1", "prophylaxis"] == "cefuroxime | metronidazole"
    assert out.loc["e1", "prophylaxis_group"] == "cefuroxime +/- metronidazole"
    assert out.loc["e1", "delta_hours_prophylaxis"] == pytest.approx(25 / 60)   # anchor: closest dose (metronidazole)


def test_non_therapeutic_agent_preferred_over_closer_treatment_agent(cfg):
    out = choose_prophylaxis(doses([
        ("e1", "Meropenem", -5, "IV", 1),
        ("e1", "Cefuroxime", -60, "IV", 1),
    ]), cfg)
    assert out.loc[0, "prophylaxis"] == "cefuroxime"


def test_surgery_without_course_gets_no_prophylaxis(cfg):
    episodes = pd.DataFrame({"infection_id": ["a", "b", "c"], "surgery_event_id": ["e1", "e1", "e2"]})
    courses = choose_prophylaxis(doses([("e1", "Cefuroxime", -30, "IV", 1)]), cfg)
    out = add_prophylaxis(episodes, courses, cfg).set_index("infection_id")
    assert out.loc["a", "prophylaxis_group"] == out.loc["b", "prophylaxis_group"] == "cefuroxime +/- metronidazole"
    assert out.loc["c", "prophylaxis_group"] == "no_prophylaxis" and pd.isna(out.loc["c", "delta_hours_prophylaxis"])


def test_agent_matching_two_classes_raises(cfg):
    cfg["prophylaxis"]["agent_classes"]["cefuroxime"].append("metronidazole")
    with pytest.raises(ValueError, match="more than one class"):
        choose_prophylaxis(doses([("e1", "Metronidazole", -30, "IV", 1)]), cfg)


def test_past_abx_window_is_1_to_90_days_before_admission(cfg):
    adm = pd.Timestamp("2020-06-01 10:00")
    episodes = pd.DataFrame({"infection_id": ["a", "b", "c", "d"], "subject": ["p1", "p2", "p3", "p4"],
                             "spell_identifier": ["s1", "s2", "s3", "s4"], "admission_date": adm})
    admin = pd.DataFrame({"subject": ["p1", "p2", "p3", "p4"], "spell_identifier": ["s1", "s2", "s3", "s4"],
                          "administration_ts": [adm - pd.Timedelta(days=1, hours=-12),   # previous calendar day
                                                adm - pd.Timedelta(days=90),
                                                adm - pd.Timedelta(days=91),
                                                adm + pd.Timedelta(hours=1)]})       # during the spell
    out = flag_past_abx(episodes, admin, cfg)
    assert list(out["past_abx"]) == [1, 1, 0, 0]
