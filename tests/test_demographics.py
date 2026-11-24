"""Tests for esbl_rf.demographics."""
import logging

import pandas as pd
import pytest

from esbl_rf.demographics import attach_demographics, clean_demographics, merge_sparse_ethnicity


def raw(rows):
    return pd.DataFrame(rows, columns=["subject", "ethnicity_desc", "gender_desc", "death_date"])


def test_groups_ethnicity_and_gender(cfg):
    out = clean_demographics(raw([
        ("a", "White - British", "Female", None),
        ("b", "Indian", "Male", None),
        ("c", "Not Stated", "Unspecified", None),
    ]), cfg)
    assert out.set_index("subject")["ethnicity_desc"].fillna("missing").to_dict() == {"a": "white", "b": "asian", "c": "missing"}
    assert out.set_index("subject")["gender"].fillna("missing").to_dict() == {"a": "female", "b": "male", "c": "missing"}


def test_unmapped_ethnicity_becomes_missing_and_is_logged(cfg, caplog):
    with caplog.at_level(logging.WARNING):
        out = clean_demographics(raw([("a", "Klingon", "Male", None)]), cfg)
    assert out["ethnicity_desc"].isna().all()
    assert "Klingon".lower() in caplog.text


def test_deliberately_missing_gender_is_not_logged(cfg, caplog):
    with caplog.at_level(logging.WARNING):
        out = clean_demographics(raw([("a", "Indian", "Not Known", None)]), cfg)
    assert out["gender"].isna().all()
    assert "gender" not in caplog.text


def test_duplicate_subject_raises(cfg):
    with pytest.raises(ValueError, match="duplicate"):
        clean_demographics(raw([("a", "Indian", "Male", None), ("a", "Indian", "Male", None)]), cfg)


def test_missing_column_raises(cfg):
    with pytest.raises(ValueError, match="missing column"):
        clean_demographics(raw([("a", "Indian", "Male", None)]).drop(columns="gender_desc"), cfg)


def episodes(subjects):
    return pd.DataFrame({"infection_id": [f"e{i}" for i in range(len(subjects))], "subject": subjects})


def test_attach_keeps_rows_and_adds_columns(cfg):
    demo = clean_demographics(raw([("a", "Indian", "Male", None)]), cfg)
    out = attach_demographics(episodes(["a", "a", "b"]), demo, cfg)
    assert len(out) == 3
    assert out["ethnicity_desc"].tolist()[:2] == ["asian", "asian"]
    assert pd.isna(out.loc[2, "ethnicity_desc"])  # patient with no demographics row


def test_attach_refuses_existing_column(cfg):
    demo = clean_demographics(raw([("a", "Indian", "Male", None)]), cfg)
    eps = episodes(["a"]).assign(gender="x")
    with pytest.raises(ValueError, match="already"):
        attach_demographics(eps, demo, cfg)


def test_sparse_ethnicity_merged_into_other(cfg):
    cfg["demographics"]["ethnicity_min_count"] = 3
    eps = pd.DataFrame({"ethnicity_desc": ["white"] * 3 + ["mixed"] * 2 + ["other"] + [pd.NA]})
    out = merge_sparse_ethnicity(eps, cfg)
    assert (out["ethnicity_desc"] == "white").sum() == 3
    assert (out["ethnicity_desc"] == "other").sum() == 3   # 2 mixed merged + 1 other (itself sparse, kept)
    assert out["ethnicity_desc"].isna().sum() == 1


def test_mixed_kept_when_not_sparse(cfg):
    cfg["demographics"]["ethnicity_min_count"] = 2
    eps = pd.DataFrame({"ethnicity_desc": ["white"] * 3 + ["mixed"] * 2})
    assert (merge_sparse_ethnicity(eps, cfg)["ethnicity_desc"] == "mixed").sum() == 2
