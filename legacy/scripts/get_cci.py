"""Add Charlson comorbidities and the CCI score to the ESBL analysis cohort.

Uses comorbidicare (https://github.com/Anisiat/comorbidicare) to derive the 17
Charlson comorbidity flags and the weighted Charlson Comorbidity Index (CCI)
for each index admission (spell), then merges them back onto the
episode-level analysis dataset. Several infection episodes can share one
spell; they all receive that spell's comorbidity values.

Inputs
------
- Episode-level cohort CSV (e.g. data/processed/lr_data.csv) with
  subject, spell_identifier, admission_date and discharge_date.
- One or more raw iCARE evidence tables as CSV: diagnoses, problems,
  prescriptions. Pass only the ones you have.

Outputs
-------
- <output>.csv: the cohort with 17 Charlson flags, cci_score and cci_group added.
- <output>_summary.csv: prevalence of each Charlson condition and CCI score
  distribution, overall and by ESBL status (if esbl_status is present).

Evidence cutoff
---------------
--cutoff admission (default) counts only evidence dated strictly before the
index admission, matching "comorbidities present on admission" in the
manuscript. Codes first recorded during the index spell are NOT counted.
--cutoff discharge also counts codes recorded during the stay (captures
long-standing conditions only coded at this admission, but can include
conditions that began during the stay). Report which cutoff was used.

Example
-------
python get_cci.py \
    --cohort data/processed/lr_data.csv \
    --diagnoses data/raw/diagnoses.csv \
    --problems data/raw/problems.csv \
    --prescriptions data/raw/prescriptions.csv \
    --cutoff admission \
    --output data/processed/lr_data_cci.csv
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

import comorbidicare
from comorbidicare import build_comorbidity_table


SPELL_COLS = ["subject", "spell_identifier", "admission_date", "discharge_date"]

CHARLSON_COLS = [
    "myocardial_infarction",
    "congestive_heart_failure",
    "peripheral_vascular_disease",
    "cerebrovascular_disease",
    "dementia",
    "chronic_pulmonary_disease",
    "rheumatic_disease",
    "peptic_ulcer_disease",
    "mild_liver_disease",
    "diabetes_without_complication",
    "diabetes_with_complication",
    "hemiplegia_or_paraplegia",
    "renal_disease",
    "malignancy",
    "moderate_or_severe_liver_disease",
    "metastatic_solid_tumor",
    "aids_hiv",
]

# CCI bands for regression. Edit here if a different grouping is agreed.
CCI_BINS = [-0.5, 0.5, 2.5, float("inf")]
CCI_LABELS = ["0", "1-2", "3+"]

# Existing study comorbidity flags vs. the closest Charlson condition(s),
# used only as a sanity check on agreement (not expected to match exactly).
LEGACY_CHECKS = {
    "copd": ["chronic_pulmonary_disease"],
    "asthma": ["chronic_pulmonary_disease"],
    "type2_diabetes": ["diabetes_without_complication", "diabetes_with_complication"],
    "cancer": ["malignancy", "metastatic_solid_tumor"],
    "ischaemic_heart_disease": ["myocardial_infarction"],
}


# ---------------------------------------------------------
# Loading
# ---------------------------------------------------------


def read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV and normalise column names (strip + lowercase)."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    df = pd.read_csv(path, low_memory=False)
    df.columns = [str(col).strip().lower() for col in df.columns]
    return df


def load_evidence(path: Path | None, subjects: pd.Index, name: str) -> pd.DataFrame | None:
    """Load one evidence table, restricted to cohort subjects to save memory."""
    if path is None:
        return None

    df = read_csv(path)
    if "subject" not in df.columns:
        raise ValueError(f"{name} table has no 'subject' column: {path}")

    n_before = len(df)
    df = df[df["subject"].isin(subjects)]
    logging.info(
        "%s: %d rows loaded, %d for cohort subjects", name, n_before, len(df)
    )
    return df


# ---------------------------------------------------------
# Cohort preparation
# ---------------------------------------------------------


def build_spell_cohort(episodes_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the episode-level cohort to one row per subject + spell."""
    missing = set(SPELL_COLS).difference(episodes_df.columns)
    if missing:
        raise ValueError("Cohort is missing columns: " + ", ".join(sorted(missing)))

    spells = episodes_df[SPELL_COLS].drop_duplicates()

    if spells[SPELL_COLS].isna().any().any():
        n_bad = spells[SPELL_COLS].isna().any(axis=1).sum()
        raise ValueError(f"{n_bad} spells have missing identifiers or dates.")

    conflicting = spells.duplicated(["subject", "spell_identifier"], keep=False)
    if conflicting.any():
        raise ValueError(
            f"{spells.loc[conflicting, 'spell_identifier'].nunique()} spells have "
            "more than one admission/discharge date in the cohort. Fix before scoring."
        )

    return spells


# ---------------------------------------------------------
# Merge + summaries
# ---------------------------------------------------------


def merge_onto_episodes(episodes_df: pd.DataFrame, features_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join spell-level Charlson features onto the episode-level data."""
    new_cols = CHARLSON_COLS + ["cci_score"]
    clashes = set(new_cols).intersection(episodes_df.columns)
    if clashes:
        raise ValueError(
            "Cohort already has Charlson columns: " + ", ".join(sorted(clashes))
            + ". Drop them first or use a fresh cohort file."
        )

    merged = episodes_df.merge(
        features_df[["subject", "spell_identifier"] + new_cols],
        on=["subject", "spell_identifier"],
        how="left",
        validate="many_to_one",
    )

    if len(merged) != len(episodes_df):
        raise RuntimeError("Row count changed during merge.")
    if merged["cci_score"].isna().any():
        raise RuntimeError("Some episodes did not receive a CCI score.")

    merged[new_cols] = merged[new_cols].astype(int)
    merged["cci_group"] = pd.cut(
        merged["cci_score"], bins=CCI_BINS, labels=CCI_LABELS
    ).astype(str)

    return merged


def summarise(df: pd.DataFrame, outcome: str = "esbl_status") -> pd.DataFrame:
    """Prevalence of each Charlson flag and CCI distribution, overall and by outcome."""
    groups = {"overall": df}
    if outcome in df.columns:
        for value, sub in df.groupby(outcome):
            groups[f"{outcome}={value}"] = sub

    rows = []
    for label, sub in groups.items():
        n = len(sub)
        for col in CHARLSON_COLS:
            k = int(sub[col].sum())
            rows.append({"group": label, "variable": col, "n": k, "N": n, "pct": 100 * k / n})
        for band in CCI_LABELS:
            k = int((sub["cci_group"] == band).sum())
            rows.append({"group": label, "variable": f"cci_group={band}", "n": k, "N": n, "pct": 100 * k / n})
        q1, med, q3 = sub["cci_score"].quantile([0.25, 0.5, 0.75])
        rows.append({
            "group": label, "variable": "cci_score median [IQR]", "n": None, "N": n,
            "pct": None, "value": f"{med:g} [{q1:g}, {q3:g}]",
        })

    return pd.DataFrame(rows)


def log_legacy_agreement(df: pd.DataFrame) -> None:
    """Log agreement between existing study comorbidity flags and Charlson flags."""
    for legacy, charlson in LEGACY_CHECKS.items():
        if legacy not in df.columns:
            continue
        sub = df[df[legacy].notna()]
        old = sub[legacy].astype(int)
        new = sub[charlson].max(axis=1)
        logging.info(
            "Agreement %-24s vs %-55s both=%d old_only=%d new_only=%d neither=%d",
            legacy,
            " | ".join(charlson),
            int(((old == 1) & (new == 1)).sum()),
            int(((old == 1) & (new == 0)).sum()),
            int(((old == 0) & (new == 1)).sum()),
            int(((old == 0) & (new == 0)).sum()),
        )


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="Add Charlson comorbidities and CCI to the ESBL analysis cohort."
    )
    parser.add_argument("--cohort", required=True, type=Path,
                        help="Episode-level cohort CSV (e.g. data/processed/lr_data.csv).")
    parser.add_argument("--diagnoses", type=Path, help="Raw iCARE diagnoses CSV.")
    parser.add_argument("--problems", type=Path, help="Raw iCARE problems CSV.")
    parser.add_argument("--prescriptions", type=Path, help="Raw iCARE prescribing CSV.")
    parser.add_argument("--cutoff", choices=["admission", "discharge"], default="admission",
                        help="Evidence cutoff (default: admission).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output CSV (default: <cohort>_cci_<cutoff>.csv next to the cohort).")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not any([args.diagnoses, args.problems, args.prescriptions]):
        raise SystemExit("Pass at least one of --diagnoses, --problems, --prescriptions.")

    output_path = args.output or args.cohort.with_name(
        f"{args.cohort.stem}_cci_{args.cutoff}.csv"
    )
    summary_path = output_path.with_name(f"{output_path.stem}_summary.csv")

    logging.info("comorbidicare version %s", comorbidicare.__version__)
    logging.info("Evidence cutoff: %s", args.cutoff)

    # Load cohort and build one row per spell
    episodes_df = read_csv(args.cohort)
    spells_df = build_spell_cohort(episodes_df)
    logging.info(
        "Cohort: %d episodes, %d spells, %d patients",
        len(episodes_df), len(spells_df), spells_df["subject"].nunique(),
    )

    # Load evidence tables
    subjects = pd.Index(spells_df["subject"].unique())
    diagnoses_df = load_evidence(args.diagnoses, subjects, "diagnoses")
    problems_df = load_evidence(args.problems, subjects, "problems")
    prescriptions_df = load_evidence(args.prescriptions, subjects, "prescriptions")

    # Derive Charlson flags + CCI per spell
    features_df = build_comorbidity_table(
        cohort_df=spells_df,
        diagnoses_df=diagnoses_df,
        problems_df=problems_df,
        prescriptions_df=prescriptions_df,
        cci_score=True,
        cutoff=args.cutoff,
    )

    # Attach to episodes and save
    out_df = merge_onto_episodes(episodes_df, features_df)
    log_legacy_agreement(out_df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    summary_df = summarise(out_df)
    summary_df.to_csv(summary_path, index=False)

    logging.info("CCI distribution (episodes):\n%s",
                 out_df["cci_group"].value_counts().reindex(CCI_LABELS).to_string())
    logging.info("Saved %s", output_path)
    logging.info("Saved %s", summary_path)


if __name__ == "__main__":
    main()
