"""Generate synthetic CSVs with the same columns as the SDE exports (no real data).

Covers the messy cases the pipeline must handle: ``'none'`` dates, screening
samples, samples before 48 h, unknown ESBL status, an ``other`` specimen site,
episodes with several samples, patients with two episodes in one spell,
prior ESBL cultures and prior inpatient stays. Headers are upper-case, as Snowflake exports them.

Usage: ``python synthetic/generate.py [out_dir] [n_patients]``
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ORGANISMS = ["escherichia coli", "klebsiella pneumoniae", "klebsiella oxytoca", "proteus mirabilis"]
SITES = [("urine", "urine culture"), ("blood", "blood culture"), ("wound swab", "wound swab"), ("pus", "wound swab")]
TFC = ["general surgery", "urology", "colorectal surgery", "trauma & orthopaedics", "vascular surgery"]
ETHNICITY = ["White - British"] * 6 + ["Indian"] * 2 + ["Black or Black British - African"] * 2 + ["Mixed - White and Asian", "Not Stated"]
RESISTANT = ["ceftriaxone : resistant", "ceftazidime : >16"]
SUSCEPTIBLE = ["ceftriaxone : sensitive", "ceftazidime : sensitive", "cefpodoxime : sensitive"]


def generate(out_dir: Path | str, n_patients: int = 80, seed: int = 1) -> dict[str, pd.DataFrame]:
    """Write the CSVs to ``out_dir`` and return them (lower-case headers) keyed by file name without ``.csv``."""
    rng = np.random.default_rng(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spell_micro, ekp, results, surgeries, demo, prior = [], [], [], [], [], []
    diag, problems, rx = [], [], []
    surgery_abx, abx_admin, temperature, crp = [], [], [], []
    discharge_method = {}   # spell -> NHS discharge method code (4 = died)
    lab_id = 0

    def add_sample(subject, spell, adm, dis, age, imd, collect, organism, site, status, code="cul"):
        """One sample (a row in spell_microbiology and microbiology_ekp) and its result strings."""
        nonlocal lab_id
        lab_id += 1
        site_name, culture_type = site
        base = dict(subject=subject, lab_test_id=lab_id, organism_bug=organism, site=site_name, culture_type=culture_type,
                    culture_code=code, collect_ts=str(collect), esbl_statement=0)
        ekp.append(base)
        if spell is not None:
            spell_micro.append({**base, "spell_identifier": spell, "admission_ts": str(adm), "discharge_ts": str(dis),
                                "age_at_admission_n": age, "index_of_multiple_deprivation_decile": imd,
                                "discharge_method": discharge_method.get(spell, 1)})
        strings = {"esbl": RESISTANT, "non": SUSCEPTIBLE, "unknown": []}[status]
        for s in strings:
            results.append(dict(subject=subject, lab_test_id=lab_id, organism_bug=organism, sensitivity=s))

    for i in range(n_patients):
        subject, spell = f"p{i:03d}", f"s{i:03d}"
        adm = pd.Timestamp("2016-01-01") + pd.Timedelta(days=int(rng.integers(0, 3000)), hours=8)
        surg_start = adm + pd.Timedelta(days=1)
        dis = adm + pd.Timedelta(days=7)
        age, imd = int(rng.integers(18, 90)), int(rng.integers(1, 11))
        if i % 20 == 0:
            imd = "none"  # iCARE stores a missing IMD as the text 'none'
        organism = ORGANISMS[int(rng.integers(0, 4))]
        site = SITES[int(rng.integers(0, len(SITES)))]
        status = ["esbl", "non", "unknown"][int(rng.choice(3, p=[0.3, 0.6, 0.1]))]
        surg_stop = surg_start + pd.Timedelta(minutes=int(rng.integers(45, 420)))

        # Like Snowflake HASH(): a 19-digit integer, more digits than a float keeps.
        event = 7_000_000_000_000_000_001 + i * 1_000_003
        surgeries.append(dict(subject=subject, spell_identifier=spell, surgery_event_id=event,
                              surgery_anchor_ts=str(surg_start), surgery_start_ts=str(surg_start),
                              surgery_stop_ts=str(surg_stop), procedure_desc="hernia repair",
                              surgical_area="abdomen", treatment_function_code_des=TFC[int(rng.integers(0, len(TFC)))]))
        first = surg_start + pd.Timedelta(days=3)
        discharge_method[spell] = 4 if rng.random() < 0.1 else (9 if rng.random() < 0.05 else 1)

        def abx(name, when, route="IV", in_window=1):
            surgery_abx.append(dict(subject=subject, spell_identifier=spell, surgery_event_id=event,
                                    surgery_start_ts=str(surg_start), surgery_stop_ts=str(surg_stop),
                                    medication_name_short=name, route=route, administration_status="Given",
                                    administration_ts=str(when), in_window=in_window, before_window=0))

        r = rng.random()
        if r < 0.6:      # cefuroxime + metronidazole at induction (the reference group)
            abx("Cefuroxime", surg_start - pd.Timedelta(minutes=30))
            abx("Metronidazole", surg_start - pd.Timedelta(minutes=25))
            if rng.random() < 0.2:   # plus gentamicin
                abx("Gentamicin", surg_start - pd.Timedelta(minutes=20))
        elif r < 0.75:   # co-amoxiclav; an oral dose and a non-antibiotic in the window must be ignored
            abx("Co-Amoxiclav (contains penicillin)", surg_start - pd.Timedelta(minutes=40))
            abx("Ciprofloxacin", surg_start - pd.Timedelta(minutes=40), route="Oral")
            abx("Fluconazole", surg_start - pd.Timedelta(minutes=40))
        elif r < 0.85:   # treatment agent outside the window only: no prophylaxis
            abx("Meropenem", surg_start - pd.Timedelta(hours=10), in_window=0)
        if rng.random() < 0.3:   # antibiotic 40 days before admission: past_abx = 1
            abx_admin.append(dict(subject=subject, spell_identifier=spell, medication_name_short="Amoxicillin (contains penicillin)",
                                  route="Oral", administration_status="Given", administration_ts=str(adm - pd.Timedelta(days=40))))
        abx_admin.append(dict(subject=subject, spell_identifier=spell, medication_name_short="Cefuroxime", route="IV",
                              administration_status="Given", administration_ts=str(surg_start)))   # in the spell: not past
        temperature.append(dict(subject=subject, spell_identifier=spell, temp_performed_ts=str(first - pd.Timedelta(hours=6)),
                                temperature=str(round(37 + rng.random() * 2, 1))))
        temperature.append(dict(subject=subject, spell_identifier=spell, temp_performed_ts=str(first - pd.Timedelta(hours=1)),
                                temperature="101.3"))   # Fahrenheit: implausible, dropped
        temperature.append(dict(subject=subject, spell_identifier=spell, temp_performed_ts=str(adm + pd.Timedelta(hours=2)),
                                temperature="36.5"))    # before surgery: not used
        if rng.random() < 0.7:
            crp.append(dict(subject=subject, spell_identifier=spell, sample_collected_ts=str(first + pd.Timedelta(hours=10)),
                            crp=float(rng.integers(5, 300))))
        add_sample(subject, spell, adm, dis, age, imd, first, organism, site, status)
        if rng.random() < 0.5:   # second sample, same episode
            add_sample(subject, spell, adm, dis, age, imd, first + pd.Timedelta(days=5), organism, site, "non")
        if rng.random() < 0.2:   # gap > 14 days: second episode in the same spell (after discharge)
            add_sample(subject, spell, adm, dis, age, imd, first + pd.Timedelta(days=20), organism, site, "esbl")
        if rng.random() < 0.1:   # screening swab
            add_sample(subject, spell, adm, dis, age, imd, first, organism, ("rectal swab", "screen"), "esbl", code="rgns")
        if rng.random() < 0.1:   # before 48 h
            add_sample(subject, spell, adm, dis, age, imd, adm + pd.Timedelta(hours=12), organism, site, "non")
        if rng.random() < 0.1:   # other specimen site
            add_sample(subject, spell, adm, dis, age, imd, first + pd.Timedelta(days=1), organism, ("ear swab", "ear swab"), "non")
        if rng.random() < 0.25:  # earlier ESBL culture, not in any spell
            add_sample(subject, None, None, None, None, None, adm - pd.Timedelta(days=200), organism, site, "esbl")
        if rng.random() < 0.25:  # earlier stay inside the 90-day window
            prior.append(dict(subject=subject, spell_identifier=spell, index_admission_d=adm.normalize(),
                              prior_spell_identifier=f"old{i:03d}", prior_admission_d=(adm - pd.Timedelta(days=40)).normalize(),
                              prior_discharge_d=(adm - pd.Timedelta(days=35)).normalize()))
        if rng.random() < 0.3:   # myocardial infarction (ICD-10 I21) diagnosed in an earlier year
            diag.append(dict(subject=subject, spell_identifier=f"old{i:03d}", diagnosis_code_icd="I21", diagnosis_code_snomed="",
                             diagnosis_date=str(adm - pd.Timedelta(days=400))))
        if rng.random() < 0.1:   # same diagnosis only after admission: must not count
            diag.append(dict(subject=subject, spell_identifier=spell, diagnosis_code_icd="E109", diagnosis_code_snomed="",
                             diagnosis_date=str(dis)))
        if rng.random() < 0.2:   # dementia by a 17-digit SNOMED code only: lost if the column is read as float
            diag.append(dict(subject=subject, spell_identifier=f"old{i:03d}", diagnosis_code_icd="", diagnosis_code_snomed="16276361000119109",
                             diagnosis_date=str(adm - pd.Timedelta(days=500))))
        if rng.random() < 0.2:   # peripheral vascular disease on the problem list (SNOMED)
            problems.append(dict(subject=subject, encntr_id=i, problem_code="95443002", problem_desc="pvd",
                                 problem_dt_tm=str(adm - pd.Timedelta(days=300))))
        if rng.random() < 0.15:  # diabetes drug
            rx.append(dict(subject=subject, medication_name_short="gliclazide", order_dt_tm=str(adm - pd.Timedelta(days=200))))
        demo.append(dict(subject=subject, death_date="none" if rng.random() < 0.9 else str(dis.normalize()),
                         ethnicity="x", ethnicity_desc=ETHNICITY[int(rng.integers(0, len(ETHNICITY)))],
                         gender="1", gender_desc=["Male", "Female", "Not Known"][int(rng.choice(3, p=[0.48, 0.48, 0.04]))]))

    tables = {"spell_microbiology": spell_micro, "microbiology_ekp": ekp, "microbiology_results": results,
              "surgery_cohort": surgeries, "demographics": demo, "prior_inpatient_exposure": prior,
              "diagnoses": diag, "problems": problems, "prescriptions": rx, "surgery_abx": surgery_abx,
              "abx_administrations": abx_admin, "temperature": temperature, "crp": crp}
    tables["qa_flowchart_sql"] = _sql_flowchart(pd.DataFrame(surgeries), pd.DataFrame(spell_micro))
    frames = {}
    for name, rows in tables.items():
        df = pd.DataFrame(rows)
        frames[name] = df
        df.rename(columns=str.upper).to_csv(out_dir / f"{name}.csv", index=False)
    return frames


def _sql_flowchart(surgeries: pd.DataFrame, spell_micro: pd.DataFrame) -> list[dict]:
    """Rows of qa_flowchart_sql.csv (09_qa.sql: AT_QA_FLOWCHART), consistent with the other tables.

    Step 3 counts spell_microbiology exactly, as the real SQL does; steps 1-2 add
    admissions that have no eligible culture or an emergency procedure.
    """
    def n(df: pd.DataFrame) -> tuple[int, int]:
        return len(df[["subject", "spell_identifier"]].drop_duplicates()), df["subject"].nunique()

    adm2, pat2 = n(surgeries)
    adm3, pat3 = n(spell_micro)
    events2 = surgeries["surgery_event_id"].nunique()
    return [
        dict(step=1, description="Adult elective admissions with >=1 surgery in window", n_admissions=adm2 + 40,
             n_patients=pat2 + 30, n_surgery_events=events2 + 45, n_cultures=None),
        dict(step=2, description="After excluding surgery events with an emergency procedure", n_admissions=adm2,
             n_patients=pat2, n_surgery_events=events2, n_cultures=None),
        dict(step=3, description="Admissions with >=1 culture of an eligible organism", n_admissions=adm3,
             n_patients=pat3, n_surgery_events=None, n_cultures=len(spell_micro)),
        dict(step=31, description="  of which culture has AST info or an ESBL statement (informational)",
             n_admissions=adm3 - 2, n_patients=pat3 - 2, n_surgery_events=None, n_cultures=len(spell_micro) - 3),
    ]


if __name__ == "__main__":
    generate(sys.argv[1] if len(sys.argv) > 1 else "data/raw", int(sys.argv[2]) if len(sys.argv) > 2 else 80)
