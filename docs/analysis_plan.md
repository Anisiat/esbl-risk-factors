# Analysis plan — esbl-risk-factors

Single source of truth for study definitions, variables and outputs. Derived
from the September 2026 manuscript Methods, Tables 1–2, Figure 1 and Tables
S1–S3. Code must follow this document; if the two disagree, stop and ask.

`TODO` = decision needed from the author before coding. `CHANGE` = differs
from the previous analysis run.

---

## 1. Design

Retrospective multicentre cohort study. Imperial College Healthcare NHS Trust:
Charing Cross, Hammersmith and St Mary's hospitals. Data from the iCARE secure
data environment. Within the cohort, ESBL-positive and non-ESBL infection
episodes are compared (case-case comparison). Unit of analysis = infection
episode. Patients may contribute more than one episode.

## 2. Cohort derivation (in flowchart order)

Each step logs the number of admissions, patients, samples and episodes
remaining, and is written to `outputs/export/flowchart_counts.csv`.

| Step | Rule | Previous run (reference) |
|---|---|---|
| 1 | Adult (age_at_admission ≥ 18) elective admissions (admission_method ∈ {11, 12, 13}) with ≥1 surgery between admission and discharge, within study window | 255,888 admissions |
| 2 | Exclude surgeries in an event where any procedure description contains "emergency" | (not reported separately) |
| 3 | Admissions with ≥1 positive microbiology culture during the admission | 7,881 (3.1%) |
| 4 | Restrict to eligible organisms (§4) | not reported |
| 5 | Restrict to eligible samples: non-screening, collected after surgery, ≥48 h after admission, before discharge | 3,556 samples / 2,238 patients |
| 6 | Group samples into infection episodes (§5) | 2,589 episodes |
| 7 | Assign ESBL status (§6) | 893 ESBL-positive (34.5%) |

- **Study window:** `study_start = 2015-01-01`; `study_end` = TODO. The
  manuscript says both "August 2025" and "September 2025"; the SQL currently
  uses surgery stop ≤ 2025-08-31 00:00. Pick one; implement as
  `< study_end + 1 day`.
- **Window anchor:** TODO. Surgery start or surgery stop must fall within the
  admission. The SQL currently uses stop; start is more conventional.
- **CHANGE:** surgery–episode linkage is deduplicated so each surgery appears
  once per spell (`icare_episodes_anon` is episode-level). Specialty comes
  from the consultant episode containing the surgery.
- **"Postoperative" with multiple surgeries per spell:** TODO. Is a sample
  attributed to the most recent preceding surgery (recommended), or to the
  first surgery in the spell? This decides which specialty, duration and
  prophylaxis an episode inherits.
- **Screening samples:** TODO. Specify the rule (order_name / site_cleaned
  values) that defines a non-diagnostic screening sample. List the values in
  `config.yaml`.
- **Reporting:** every count above is regenerated and compared with the
  reference column; the run log reports the absolute and % change for each
  step.

## 3. Data sources

| Table | Grain | Used for |
|---|---|---|
| icare_surgery_anon | procedure row | surgery times, procedure_desc, surgical_area |
| icare_episodes_anon | consultant episode (many per spell) | admission/discharge, admission method, age, IMD, specialty (TFC) |
| icare_microbiology_anon | sample × organism × antibiotic | cultures, organisms, susceptibilities, site |
| icare_demographic_anon | patient | sex, ethnicity |
| icare_pharmacy_administration_anon | administration | prophylaxis, prior antibiotics |
| icare_episodes_diagnosis_anon, icare_problem_anon, prescribing | event | Charlson comorbidities (comorbidicare) |
| icare_emergency_anon, prior episodes | attendance / spell | 90-day healthcare exposure |
| icare_pathology_blood_anon, icare_vital_signs_anon | result | CRP, temperature |

## 4. Eligible organisms

E. coli, K. pneumoniae, K. oxytoca, P. mirabilis. Mapping from the raw
`organism_bug` strings to these four is listed in `config.yaml`; the run log
reports any unmapped strings that contain the genus names.

## 5. Infection episode definition

Within patient and organism, sorted by `sample_collected_dt`:

- A new episode starts if (i) the organism differs from all open episodes, or
  (ii) the same organism is isolated more than 14 days after the **preceding
  positive culture of that organism** (rolling window).
- Repeat isolation of the same organism within the window joins the episode.
- Applied across all specimen types.
- TODO: boundary. Methods say ">14 days"; the Figure S1 legend says "≥14
  days". Choose one and fix both texts.
- TODO: can an episode span two spells? Recommended: no. Episodes are built
  within the surgical spell, since eligibility is spell-restricted.

**Episode-level attributes**

- Index sample = first sample in the episode. Index date =
  `sample_collected_dt` of the index sample.
- Specimen site = site of the index sample (TODO confirm, versus a "most
  invasive" hierarchy).
- Time from surgery to infection = index date − surgery start (hours/days).

## 6. Outcome: ESBL status (phenotypic)

Isolate is ESBL-positive if any of:

- cefpodoxime resistant;
- positive ESBL confirmatory test;
- resistant to ≥1 third-generation cephalosporin.

TODO: list the exact antibiotic name strings for third-generation
cephalosporins (e.g. cefotaxime, ceftriaxone, ceftazidime) and for the ESBL
confirmatory test, as they appear in the data.

- Episode ESBL = 1 if any isolate in the episode is ESBL-positive.
- Non-ESBL = no ESBL marker **and** susceptible to the relevant
  third-generation cephalosporins and/or cefpodoxime.
- TODO: episodes with no relevant susceptibility result. Exclude them
  (implied by the Methods) or treat as non-ESBL? Count them in the flowchart
  either way.

## 7. Variables

Reference levels are in bold. "No record = 0" applies to all binary
exposures.

| Variable | Source | Derivation | Type / levels |
|---|---|---|---|
| Age | episodes | age_at_admission | continuous (per year) |
| Sex | demographic | gender_desc | **Female** / Male |
| Ethnicity | demographic | ethnicity_desc → 4 groups | **White**, Asian, Black, Mixed; missing kept as missing. TODO: Table 2 shows only Asian and Black. Was Mixed collapsed, dropped or merged with Other? |
| IMD decile | episodes | index_of_multiple_deprivation_decile | continuous (1–10) |
| Surgical specialty | episodes | treatment_function_code → 9 groups via mapping in `config.yaml` (TODO: supply the TFC → group table) | **General surgery**, Abdominal/GI, Cardiothoracic, Gynaecological oncology, Neurology/ENT, Obstetrics/Gynaecology, Orthopaedic/plastics, Urology/Nephrology, Vascular |
| Surgery duration | surgery | surgery_stop − surgery_start, hours. TODO: for multi-procedure events, use event start → event stop | continuous |
| Prophylaxis group | pharmacy admin | perioperative administrations grouped per Table S1. TODO: timing window (e.g. 60 min before to 24 h after incision) | **Cefuroxime ± metronidazole**, Aminoglycoside-based, Cefuroxime ± metronidazole + aminoglycoside, Co-amoxiclav-based, Limited Enterobacterales activity, Broad-spectrum Gram-negative active, No prophylaxis, Other |
| CCI | comorbidicare | 17 Charlson flags; weighted score; evidence strictly before admission (`--cutoff admission`) | CHANGE: replaces individual comorbidities in models; cci_group categories TODO (e.g. 0, 1–2, ≥3) |
| Individual comorbidities | comorbidicare | asthma, cancer, COPD, IHD, type 2 diabetes | binary; Table 1 only. TODO: switch Table 1 to the 17 Charlson conditions or keep the original 5? |
| Hospital exposure 90d | prior episodes | inpatient spell overlapping [admission − 90 d, admission); index spell excluded | binary |
| Emergency exposure 90d | emergency | A&E attendance in [admission − 90 d, admission) | binary |
| Prior antibiotics 90d | pharmacy admin | any systemic antibiotic in [admission − 90 d, admission) | binary |
| Prior ESBL-positive culture | microbiology | any ESBL-positive isolate (§6 rule, any organism and site) before TODO: index admission or index sample; lookback = all available history? | binary |
| Organism | microbiology | index organism | **E. coli**, K. pneumoniae, K. oxytoca, P. mirabilis |
| Specimen site | microbiology | site_cleaned → groups via `config.yaml` (TODO: supply mapping; 399 episodes were missing last run, so check why) | **Blood**, Drain, Sputum, Tips/devices, Tissue/biopsy, Urine, Wound |
| Temperature | vital signs | TODO: value and window (e.g. maximum within ±24 h of index sample) | continuous (°C) |
| CRP | pathology | TODO: value and window | continuous (mg/L) |
| Length of stay | episodes | discharge_date − admission_date of the spell, days | descriptive outcome |
| In-hospital mortality | episodes | discharge_method / discharge_destination = died (TODO: exact codes) | descriptive outcome |

TODO: temperature and CRP are in Tables 1–2 (temperature is in the
multivariable model) but are not listed in the Methods "Exposure variables".
Either add them to the Methods or drop them from the model.

## 8. Statistical analysis

1. **Descriptive (Table 1):** by ESBL status, at the episode level. Median
   [IQR] for continuous variables with Mann–Whitney U; n (%) for categorical
   variables with χ². Missing column shown. LOS and mortality are reported
   for the admission linked to each episode.
2. **Univariable:** logistic regression per variable; crude OR (95% CI).
3. **Variable selection:** p < 0.20 or clinically important (prespecified
   list in `config.yaml`: age, sex, prior ESBL, specialty, prophylaxis,
   organism, specimen site, CCI). Log the reason each variable was included.
4. **Primary model:** multivariable logistic regression, all episodes,
   cluster-robust SEs by patient (`statsmodels`, `cov_type="cluster"`).
5. **Sensitivity:** same model, restricted to the first eligible episode per
   patient.
6. **Exploratory subgroups:** by surgical specialty and by specimen site;
   report n, events and EPV per stratum; skip strata with EPV below
   `config.yaml: min_subgroup_epv` (TODO value, e.g. 5) and report them as
   not estimable.
7. **Missing data:** complete-case analysis. Report the sequential
   complete-case loss per variable.
8. **Diagnostics:** EPV overall; VIFs for all model terms (flag > 5);
   sparse categories collapsed per `config.yaml` with the collapse logged.
9. **Supplementary (Table S2):** postoperative sampling by specialty across
   all included elective surgery events. Per surgery event, the observation
   window runs from surgery start to the next surgery start within the same
   admission, or to the end of the discharge day. Report % of events with ≥1
   non-screening sample and samples per 100 postoperative patient-days.

Optional, if time allows: ESBL and non-ESBL episodes per 1,000 elective
admissions by specialty (descriptive; uses the step 1 denominator).

## 9. Output shells

All outputs go to `outputs/export/`, with small-number suppression applied
(see CLAUDE.md).

**Flowchart:** `flowchart_counts.csv` with columns step, description,
admissions, patients, samples, episodes, esbl_pos, previous_run, change_pct.

**Table 1:** columns Characteristic | Missing | Overall | ESBL-positive |
Non-ESBL | p-value. Row blocks: Patient characteristics, Surgical
characteristics, Clinical history/comorbidities (incl. CCI), Infection
characteristics (site, organism), Pathology and vitals, Clinical outcomes.

**Table 2:** columns Variable | Univariable OR (95% CI) | Multivariable
clustered aOR (95% CI) | Multivariable first-episode aOR (95% CI). Same row
blocks; "–" for variables not in the model; reference categories in a
footnote.

**Table S2:** columns Specialty | n surgery events | % sampled | samples per
100 patient-days.

**Table S3:** columns Stratum | Variable/category | OR (95% CI) | p | n |
events | EPV.

**Figure:** forest plot of primary-model aORs, with reference categories
annotated and significant estimates (p < 0.05) highlighted.

**Model diagnostics:** `model_diagnostics.csv` with N used, events, EPV, max
VIF, and the variables dropped or collapsed.

## 10. Changes from the previous run (summary)

1. Cohort SQL: episode-level duplication fixed; columns aligned between
   steps; date bounds corrected.
2. Specialty assigned from the consultant episode containing the surgery.
3. CCI (comorbidicare, admission cutoff) replaces individual comorbidities in
   the models.
4. Study end date harmonised across the SQL, manuscript and flowchart.
5. All counts are compared against the previous run so changes can be
   explained in the revised manuscript.
