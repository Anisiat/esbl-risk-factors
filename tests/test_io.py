"""Reading raw CSVs: text columns keep long codes intact."""
import pandas as pd
import pytest

from esbl_rf.io import read_raw


def test_text_cols_keep_long_snomed_codes_with_upper_case_headers(cfg, tmp_path):
    path = tmp_path / "diagnoses.csv"
    # Snowflake-style upper-case headers; a blank cell would make pandas read the column as float.
    path.write_text("SUBJECT,DIAGNOSIS_CODE_SNOMED\np1,16276361000119109\np2,\n")
    cfg["raw_csv"] = {"diagnoses": path}

    as_float = read_raw(cfg, "diagnoses")
    assert as_float["diagnosis_code_snomed"].dtype == float   # the problem this guards against

    df = read_raw(cfg, "diagnoses", text_cols=["diagnosis_code_snomed"])
    assert df.loc[0, "diagnosis_code_snomed"] == "16276361000119109"
    assert pd.isna(df.loc[1, "diagnosis_code_snomed"])


def test_missing_text_col_raises(cfg, tmp_path):
    path = tmp_path / "problems.csv"
    path.write_text("subject,problem_dt_tm\np1,2020-01-01\n")
    cfg["raw_csv"] = {"problems": path}
    with pytest.raises(ValueError, match="missing text column"):
        read_raw(cfg, "problems", text_cols=["problem_code"])
