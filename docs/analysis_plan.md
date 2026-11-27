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

**Terminology (decided 2026-11-16).** The cohort is described as
*postoperative, healthcare-associated* Enterobacterales infection episodes:
clinical (non-screening) isolates of the four study organisms, collected ≥48 h
after admission and within 30 days of surgery. Samples collected after
discharge are included. Do not call the whole cohort "nosocomial" (hospital-
acquired); in-hospital samples (`collected_during_admission = 1`) may be
described as hospital-onset, and an in-hospital-only sensitivity analysis is
possible. Limitations to state: the microbiology table has no location column,
so post-discharge samples cannot be split by setting; an eligible isolate is
treated as an infection episode without clinical adjudication (so not
"surgical site infection"); the 48 h rule is measured from the surgical
admission and is uninformative after discharge.

## 2. Cohort derivation (in flowchart order)

Each step logs the number of admissions, patients, samples and episodes
remaining, and is written to `outputs/export/flowchart_counts.csv`.

| Step | Rule | Previous run (reference) |
|---|---|---|
| 1 | Adult (age_at_admission ≥ 18) elective admissions (admission_method ∈ {11, 12, 13}) with ≥1 surgery between admission and discharge, within study window | 255,888 admissions |
| 2 | Exclude surgeries in an event where any procedure description contains "emergency" | (not reported separately) |
| 3 | Admissions with ≥1 positive microbiology culture during the admission or up to 30 days after the spell's last surgery (CHANGE, decided 2026-11-16: post-discharge samples included) | 7,881 (3.1%) |
| 4 | Restrict to eligible organisms (§4) | not reported |
| 5 | Restrict to eligible samples: non-screening, collected after surgery, ≥48 h after admission, ≤30 days after the most recent preceding surgery (CHANGE: no longer restricted to before discharge) | 3,556 samples / 2,238 patients |
| 6 | Group samples into infection episodes (§5) | 2,589 episodes |
| 7 | Assign ESBL status (§6) | 893 ESBL-positive (34.5%) |

- **Study window:** `study_start = 2015-01-01`; `study_end = 2025-09-30`
  (decided 2026-11-13; the manuscript SQL used surgery stop ≤ 2025-08-31).
  Implemented as `< study_end + 1 day`.
- **Window anchor:** surgery **start** must fall within the admission and the
  study window (decided 2026-11-13; the manuscript SQL used stop).
- **CHANGE:** surgery–episode linkage is deduplicated so each surgery appears
  once per spell (`icare_episodes_anon` is episode-level). Specialty comes
  from the consultant episode containing the surgery.
- **"Postoperative" with multiple surgeries per spell (decided 2026-11-13):**
  each sample is attributed to the most recent surgery before it, and must be
  collected no more than 30 days after that surgery. An infection episode
  inherits the surgery attributed to its first eligible ("index") sample, so
  specialty, duration and prophylaxis come from that surgery.
  `n_surgeries` = surgeries in the spell before the index sample;
  `min_days_from_surgery_to_infection` = days from the attributed surgery to
  the index sample.
- **Screening samples:** a sample is a non-diagnostic screen if its lab culture
  code is one of `crocul`, `itucul`, `rgns`, `xincul`, or its site is a rectal
  swab / rectum / rectal. Ported from the manuscript code
  (`split_microbiology_screens`); lists in `config.yaml:
  sample_eligibility.screening`. TODO: confirm the code list against the lab's
  current order codes in the SDE.
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
- Boundary (decided 2026-11-23): a new episode needs a gap of **more than**
  14 days (as in the Methods and the code); a repeat within 14 days, day 14
  included, joins the episode. Fix the Figure S1 legend ("≥14 days").
- An episode does not span two spells: episodes are built within the surgical
  (index) spell, as in the manuscript code (decided 2026-11-16). A sample
  collected after discharge belongs to the spell of its surgery.

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
- resistant to ≥1 third-generation cephalosporin;
- the laboratory reports the statement "This is an Extended Spectrum
  Beta-Lactamase Producer (ESBL)" for that organism (`esbl_statement` from
  `03_microbiology.sql`). CHANGE, decided 2026-11-17: the manuscript code did
  not use this statement. `esbl_statement_lab` (statement anywhere in the lab
  test, any organism) is not used for labelling, only for QA.
  FINDING 2026-11-26 (open, needs a decision): ESBL status is missing for 68%
  of urine samples (869 of 1,275) but at most 4% at every other site, and for
  35% of E. coli and 34% of P. mirabilis but 2-3% of Klebsiella. Likely cause:
  cascade reporting of urinary isolates (3GCs reported only after first-line
  resistance), so the outcome is missing not at random and complete-case
  analysis may bias prevalence and the urine estimate upwards. Evidence, checks
  and sensitivity analyses: `docs/discussion_notes.md` section 1.
  FINDING 2026-11-25 (open, needs a decision): the lab stores the statement in
  `ORGANISM_BUG` as its own row, not against a named organism, so `esbl_statement`
  is always 0 and the statement currently contributes nothing. How to attribute it
  (e.g. only when the lab test has exactly one eligible organism) is undecided;
  `sql/exploration/02_esbl_statement_attribution.sql` gives the counts needed to choose.

Third-generation cephalosporin result strings. The `SENSITIVITY` column holds
`drug : result` strings. Two hand-maintained lookup tables in the SDE
(`lookup_csv: third_gen_ceph_resistant` = `3GCR.csv`, `third_gen_ceph_susceptible`
= `3GCS.csv`) list the strings counted as resistant and susceptible. Contents after the 2026-11-17 correction (see section 10, item 10):

- Resistant (3GCR, 11 strings): `cefixime : >2`, `cefixime : resistant`,
  `cefotaxime : resistant`, `cefpodoxime : resistant`, `ceftriaxone : resistant`,
  `ceftriaxone : >4`, `ceftriaxone : 4`, `ceftazidime : resistant`,
  `ceftazidime : >16`, `ceftazidime : 16`, `ceftazidime : 8`.
- Susceptible (3GCS): `cefotaxime : sensitive`, `ceftriaxone : susceptible`,
  `ceftriaxone : sensitive`, `ceftriaxone : susceptible with optimised dosing, refer
  to antimicrobial policy`, `ceftriaxone : <0.016`, `ceftriaxone : 0.016`,
  `ceftriaxone : 0.023`, `ceftriaxone : 0.032`, `ceftriaxone : 0.064`,
  `ceftriaxone : 0.094`, `ceftriaxone : 0.125`, `ceftriaxone : <=0.5`,
  `ceftazidime : sensitive`, `ceftazidime : susceptible`, `ceftazidime : 1`,
  `ceftazidime : <=0.5`, `ceftazidime : susceptible with optimised dosing, refer
  to antimicrobial policy`, plus the six strings moved from 3GCR: `ceftriaxone : 1`,
  `0.19`, `0.25`, `2`, `ceftazidime : 2`, `4` (23 strings), then on 2026-11-17 five more found by the gap check: `ceftriaxone : 0.38`, `cefixime : sensitive` and `intermediate` for cefotaxime, ceftazidime and ceftriaxone (28 strings).
  The lookups are now tracked in `lookups/3GCR.csv` and `lookups/3GCS.csv`
  (the SDE tables are the source; strings such as `not determined` and `not done`
  are deliberately in neither list, so they stay unknown).

ESBL confirmatory test (settled 2026-11-17, `config.yaml: outcome`): the
laboratory's ESBL marker field (`esbl markers (ss = present)`, where
*sensitive/susceptible* means the marker is present) and the ESBL-producer
statement (`esbl_statement`).

**Minimum evidence for a sample's status (decided 2026-11-17).** Result
categories follow EUCAST: S = susceptible (standard dosing), I = susceptible,
increased exposure, R = resistant. "Intermediate" and "susceptible with
optimised dosing" are treated as susceptible. The rules live in
`config.yaml: outcome` and are applied in `esbl_rf.outcome.label_esbl_status`.

- *ESBL-positive* needs **one** of: an R result for cefotaxime, ceftazidime,
  ceftriaxone, cefpodoxime or cefixime; a positive ESBL marker (confirmatory
  test); the laboratory's ESBL-producer statement. Positive evidence wins over
  negative evidence.
- *Non-ESBL* needs **one** of: an S result for any of cefotaxime,
  ceftazidime, ceftriaxone, cefpodoxime or cefixime; a cefalexin S result;
  a negative ESBL marker. (Decided 2026-11-26, replacing the 2026-11-17 rule
  below; cefalexin added the same day, see item 29.)
- *Unknown* otherwise (`not determined`, `not done`, strings in neither
  lookup, or no result).

**Outcome definition changed 2026-11-26: surveillance (3GC-resistance) rule.**
The non-ESBL rule now follows antimicrobial-resistance surveillance (EARS-Net,
WHO GLASS, UKHSA ESPAUR), which classes an isolate by third-generation
cephalosporin resistance among the agents tested: one susceptible 3GC result
with no resistant result is non-ESBL. Report the outcome as "3GC-resistant /
ESBL phenotype" and state that it also captures AmpC producers. Reason: the
2026-11-17 rule (non-ESBL only with cefpodoxime S, or cefotaxime or ceftriaxone
S **together with** ceftazidime S) left 775 of 2,585 episodes (30%) without a
status in the first SDE run (commit 7d27c4c), raised ESBL prevalence from
34.5% to 39.9% and may select episodes by specimen site through
site-specific test panels. CTX-M enzymes, the dominant ESBLs in UK E. coli and
Klebsiella, hydrolyse cefotaxime and ceftriaxone, so a ceftriaxone-S isolate is
unlikely to be an ESBL producer. Sensitivity analysis (TODO, not yet built):
re-run the models with the 2026-11-17 rule (cefpodoxime S alone, or both
members of a cefotaxime/ceftriaxone + ceftazidime pair S).

Rationale: EUCAST's ESBL screening uses cefotaxime, ceftazidime and cefpodoxime
together, because different ESBL families raise different cephalosporin MICs
(CTX-M mainly cefotaxime/ceftriaxone; TEM/SHV variants mainly ceftazidime). A
single susceptible result for one of them therefore cannot exclude an ESBL,
whereas cefpodoxime is the most sensitive single screening agent. TODO before
submission: check these statements and the S/I/R categories against the
current EUCAST tables and detection-of-resistance-mechanisms guideline, and
cite the version. Our rule is phenotypic and uses the categories reported by
the laboratory, not a repeat of EUCAST's MIC screening breakpoints.

`has_ast_info` in `03_microbiology.sql` is a looser screen used for early QA
counts only; it is not this definition.

- Episode ESBL = 1 if any sample in the episode is ESBL-positive; 0 if at
  least one sample is non-ESBL and none is positive; unknown if no sample gives
  a status. Samples without a status still count for the episode's span, number
  of samples and sites. Episodes with no status are excluded at their own
  logged step (section 10, item 14).

## 7. Variables

Reference levels are in bold. "No record = 0" applies to all binary
exposures.

| Variable | Source | Derivation | Type / levels |
|---|---|---|---|
| Age | episodes | age_at_admission | continuous (per year) |
| Sex | demographic | gender_desc | **Female** / Male |
| Ethnicity | demographic | ethnicity_desc → 4 groups | **White**, Asian, Black, Mixed, Other; missing kept as missing. Mixed stays its own group; a group with fewer than `demographics.ethnicity_min_count` (30) analysed episodes is merged into Other (decided 2026-11-23; check Mixed in Table 1). Why the manuscript's Table 2 shows only Asian and Black is not explained by the synced code |
| IMD decile | episodes | index_of_multiple_deprivation_decile | continuous (1–10) |
| Surgical specialty | episodes | treatment_function_code → 9 groups via mapping in `config.yaml` (TODO: supply the TFC → group table) | **General surgery**, Abdominal/GI, Cardiothoracic, Gynaecological oncology, Neurology/ENT, Obstetrics/Gynaecology, Orthopaedic/plastics, Urology/Nephrology, Vascular |
| Surgery duration | surgery | surgery_stop − surgery_start, hours. TODO: for multi-procedure events, use event start → event stop | continuous |
| Prophylaxis group | pharmacy admin | IV antibacterial administrations from 2 h before surgery start to 1 h after surgery stop (decided 2026-11-23; `00_params.sql`). Course = agents within 10 min of the dose closest to surgery start, preferring non-treatment agents; grouped per Table S1 by agent class (`config.yaml: prophylaxis`). No dose in the window = No prophylaxis | **Cefuroxime ± metronidazole**, Aminoglycoside-based, Cefuroxime ± metronidazole + aminoglycoside, Co-amoxiclav-based, Limited Enterobacterales activity, Broad-spectrum Gram-negative active, No prophylaxis, Other |
| CCI | comorbidicare | 17 Charlson flags; weighted score; evidence strictly before admission (`--cutoff admission`) | CHANGE: replaces individual comorbidities in models as the continuous `cci_score` (decided 2026-11-13; linearity checked as a sensitivity analysis). `cci_group` (0, 1–2, ≥3) is for Table 1 only |
| Individual comorbidities | comorbidicare | the 17 Charlson flags (comorbidicare names, listed in `config.yaml: table1.supplementary_conditions`) | binary; descriptive only, no p-values. Main Table 1 shows a subset (decided 2026-11-13: malignancy, metastatic solid tumour, diabetes with/without complication, chronic pulmonary disease, renal disease, congestive heart failure); the supplementary table shows all 17. Asthma, hypertension and IHD are not among the 17 flags, so they are no longer reported |
| Hospital exposure 90d | prior episodes | inpatient spell overlapping [admission − 90 d, admission); index spell excluded | binary |
| Prior antibiotics 90d | pharmacy admin | any antibacterial administration (any route) 1–90 calendar days before the admission date; secondary-care administrations only | binary |
| Prior ESBL-positive culture | microbiology | any ESBL-positive isolate (§6 rule) of the four study organisms, any site, clinical or screening sample, collected before the index admission; lookback = all available history (decided 2026-11-13). Same phenotype and organism set as the outcome; limitation: ESBL in other Enterobacterales is not counted | binary |
| Organism | microbiology | index organism | **E. coli**, K. pneumoniae, K. oxytoca, P. mirabilis |
| Specimen site | microbiology | site_cleaned → groups via `config.yaml: sites` | **Blood**, Drain, Genital (vaginal; added 2026-11-26), Sputum, Tips/devices, Tissue/biopsy, Urine, Wound |
| Temperature | vital signs | value closest to the episode's first culture within ±48 h, in the spell and after surgery start; values outside 30–45 °C dropped (decided 2026-11-23) | continuous (°C); descriptive only |
| CRP | pathology | as temperature; values outside 0–1000 mg/L dropped | continuous (mg/L); descriptive only |
| Length of stay | episodes | discharge_date − admission_date of the spell, days | descriptive outcome |
| In-hospital mortality | episodes | `discharge_method` = 4 (NHS Data Dictionary: patient died); 8/9 = missing. `death_date` is not used: it is anonymised to month/year (decided 2026-11-23) | descriptive outcome |

Temperature and CRP are descriptive only (decided 2026-11-23): they are signs
of the infection, not risk factors for it, and many values are missing. They
are in Table 1, not in the models (CHANGE: temperature was in the manuscript's
multivariable model).

## 8. Statistical analysis

1. **Descriptive (Table 1):** by ESBL status, at the episode level. Median
   [IQR] for continuous variables; n (%) for categorical variables.
   Descriptive only: no p-values (decided 2026-11-13). Missing column shown.
   LOS and mortality are reported for the admission linked to each episode.
   Computed as numbers in `esbl_rf.tables` (counts, denominators, median and
   quartiles; the manuscript used `tableone`) so the export step can suppress
   counts before formatting. Percentages use non-missing episodes; binary flags
   show the episodes with the flag. Mixed ethnicity is checked here: the run log
   warns about any categorical level with fewer than 30 episodes.
2. **Univariable:** logistic regression per variable; crude OR (95% CI).
3. **Variable selection:** p < 0.20 or clinically important (prespecified
   list in `config.yaml`: age, sex, prior ESBL, specialty, prophylaxis,
   organism, specimen site, CCI; prior antibiotics and prior hospital
   admission in the 90 days before admission added 2026-11-25 as established
   ESBL risk factors). Log the reason each variable was included.
   Implementation (`esbl_rf.models`, decided 2026-11-24): univariable models
   use patient-clustered SEs and the episodes with that variable recorded; a
   variable is screened by one joint Wald test over all its levels (the
   manuscript code used the smallest p-value of any level). Candidate
   predictors are listed in `config.yaml: logistic_regression.predictors`;
   `days_from_admission_to_first_culture` and
   `min_days_from_surgery_to_infection` describe when the infection happened,
   not a risk factor, so they are descriptive only (decided 2026-11-24). Sex is
   a categorical predictor (reference female).
4. **Primary model:** multivariable logistic regression, all episodes,
   cluster-robust SEs by patient (`statsmodels`, `cov_type="cluster"`).
5. **Sensitivity:** same model, restricted to the first eligible episode per
   patient (earliest first culture), chosen before complete-case
   filtering; ordinary SEs (one episode per patient).
6. **Exploratory subgroups:** by surgical specialty and by specimen site;
   report n, events and EPV per stratum; skip strata with EPV below
   `config.yaml: min_subgroup_epv` (5, decided 2026-11-23) and report them as
   not estimable; flag strata with EPV 5–9 as unstable. EPV counts model
   parameters (each dummy level), not variables.
7. **Missing data:** complete-case analysis. Report the sequential
   complete-case loss per variable.
8. **Diagnostics:** EPV overall; VIFs for all model terms (flag > 5);
   sparse categories collapsed per `config.yaml` with the collapse logged.
   Sparse rule (decided 2026-11-24): a non-reference level with fewer than 10
   ESBL-positive or fewer than 10 non-ESBL episodes is merged into `other`
   (`logistic_regression.sparse`), counted over all episodes before any model;
   a sparse reference level stops the run.
   VIFs are computed per design column (each dummy level).
9. **Supplementary (Table S2)** (deferred, decided 2026-11-24: needs a new SQL
   export of all elective surgery events and all non-screening samples): postoperative sampling by specialty across
   all included elective surgery events. Per surgery event, the observation
   window runs from surgery start to the next surgery start within the same
   admission, or to the end of the discharge day. Report % of events with ≥1
   non-screening sample and samples per 100 postoperative patient-days.

Optional, if time allows: ESBL and non-ESBL episodes per 1,000 elective
admissions by specialty (descriptive; uses the step 1 denominator).

## 9. Output shells

All outputs go to `outputs/export/`, with small-number suppression applied
(see CLAUDE.md).

Disclosure control (`esbl_rf.suppression`, decided 2026-11-24): counts 1–4 are
shown as `<5` with their percentage hidden; zero is shown. Secondary
suppression (`*`) hides one more cell wherever a hidden count could be worked
out from a row or column total (Table 1 rows: overall = ESBL-positive +
non-ESBL; levels of a variable with its missing count). Flowchart and
complete-case loss counts are rounded to the nearest 5 (exclusions rounded
separately), because any exclusion is the difference of two shown boxes.
Medians are not shown for groups with fewer than 5 values. Written by the last
pipeline step (`run_export`), the only writer to `outputs/export/`: CSV per
table, all tables in `tables.xlsx`, forest plot as PDF and 300 dpi PNG.
Formatted .docx is not produced (python-docx is not in the SDE pins; decided
2026-11-24).

**Flowchart:** `flowchart_counts.csv`, one row per box: SQL steps
(`qa_flowchart_sql.csv`) then Python steps, with admissions, patients,
surgery events, samples, episodes, ESBL-positive episodes and the number
excluded at each step (`esbl_rf.cohort_derivation.build_flowchart`). No
comparison with the previous run (decided 2026-11-23).

**Table 1:** columns Characteristic | Missing | Overall | ESBL-positive |
Non-ESBL (no p-value column). Row blocks: Patient characteristics, Surgical
characteristics, Clinical history/comorbidities (incl. CCI), Infection
characteristics (site, organism), Pathology and vitals, Clinical outcomes.

**Table 2:** columns Variable | Univariable OR (95% CI) | Multivariable
clustered aOR (95% CI) | Multivariable first-episode aOR (95% CI). No
p-value columns: estimates and 95% CIs only (decided 2026-11-25, to match the
manuscript's aims; the screening p-value is reported only in
`model_selection`). Same row
blocks; "–" for variables not in the model; reference categories in a
footnote.

**Table S2:** columns Specialty | n surgery events | % sampled | samples per
100 patient-days.

**Table S3:** columns Stratum | Variable/category | aOR (95% CI) | n |
events | EPV.

**Table S (definitions)** (RECORD 6.1/7.1, added 2026-11-25):
`table_s_definitions` lists the codes, lists and rules behind the cohort,
outcome and variables (admission method codes, organisms, screening
exclusions, specimen-site keywords, ESBL rules, exposure windows, prophylaxis
agent classes, specialty and ethnicity mappings, CCI source). Generated from
`config.yaml` by `esbl_rf.export.definitions_table`, so it always matches the
code that ran; it contains no data.

**Figure:** forest plot of primary-model aORs, with reference categories
annotated; estimates whose 95% CI excludes 1 drawn darker (no p-values).

**Model diagnostics:** `model_diagnostics.csv` with N used, events, EPV, max
VIF, and the variables dropped or collapsed.

## 10. Changes from the previous run (summary)

1. Cohort SQL: episode-level duplication fixed; columns aligned between
   steps; date bounds corrected.
2. Specialty assigned from the consultant episode containing the surgery.
3. CCI (comorbidicare, admission cutoff) replaces individual comorbidities in
   the models.
4. Study end date harmonised across the SQL, manuscript and flowchart.
5. Counts are not compared against the previous run in the outputs (decided
   2026-11-23); differences are explained in the revised manuscript.
6. Table 1: `delta_hours_prophylaxis`, `n_sites`, `n_samples` and
   `culture_span_days` are summarised as median [IQR] instead of mean (SD)
   (decided 2026-11-13; skewed counts/durations). `n_sites` and `n_samples`
   may be recategorised (1 vs ≥2) after the first real run if the median
   [IQR] is uninformative.
7. Eligible samples: ≥48 h after admission and ≤30 days after the most recent
   preceding surgery, including samples collected after discharge (the
   manuscript: up to 30 days after discharge) (decided 2026-11-12/16).
8. Eligibility filters are applied before infection episodes are assigned
   (the manuscript code assigned episodes first and dropped pre-operative
   samples afterwards) (decided 2026-11-17).
9. The laboratory's ESBL-producer statement counts as positive evidence in the
   ESBL labelling (decided 2026-11-17; see section 6).
10. Third-generation cephalosporin lookups corrected (decided 2026-11-17, edited
    in the SDE): `ceftriaxone : 1`, `0.19`, `0.25`, `2` and `ceftazidime : 2`,
    `4` moved from the resistant list (3GCR) to the susceptible list (3GCS).
    They are susceptible or "susceptible, increased exposure" under EUCAST, and
    the susceptible list already counts "susceptible with optimised dosing" as
    susceptible. The resistant list now has 11 strings. This may reclassify
    some manuscript samples from ESBL-positive to non-ESBL.
11. ESBL labelling matches the lookups as "drug : result" pairs (the baseline kept only the result, so a value listed resistant for one drug, e.g. ceftriaxone `1`, also made the same value resistant for ceftazidime) (decided 2026-11-17, pending your confirmation).
12. Cefixime counts as resistant evidence alongside cefotaxime, ceftazidime,
    ceftriaxone and cefpodoxime (the baseline never tested it) (decided 2026-11-17).
13. "Intermediate" results for the third-generation cephalosporins count as
    susceptible, consistent with "susceptible with optimised dosing"
    (decided 2026-11-17).
14. CHANGE vs manuscript (decided 2026-11-17/18): samples without a usable
    susceptibility result are not dropped before episodes are built (the
    manuscript code dropped them first). They stay in the sample table, count
    towards the episode's span and number of samples, and an episode is excluded
    only if none of its samples gives an ESBL status. Reported as its own
    flowchart step at sample and episode level.
15. Postoperative is judged on exact times: a sample counts as postoperative only if collected strictly after the surgery start. The baseline compared calendar dates, so a sample taken earlier on the day of surgery counted as postoperative (decided 2026-11-18; see `esbl_rf.surgery`).
16. Generic procedure labels (`other operative procedure (surg)`, `not in enumerations list`) are dropped from a spell only if the spell has a real procedure, judged per spell before surgery attribution (the manuscript judged per infection episode). Emergency labels are removed in the SQL and checked in Python (decided 2026-11-18; `esbl_rf.surgery.clean_surgeries`).
17. CHANGE vs manuscript (decided 2026-11-18): samples from `other` specimen sites
    are likewise kept until episodes are built (the manuscript dropped them
    first). An episode is excluded if its resolved site is `other`, that is, all
    its samples were `other`. `other` counts towards the episode span and
    `n_samples` but not towards `n_sites`; it ranks last in the site hierarchy.
18. Demographics (decided 2026-11-18): the three patient-specific ethnicity
    overrides in the manuscript code are removed (patient-level data does not
    belong in released code). Ethnicity is grouped from the `ethnicity_desc` values
    in the data dictionary (list in `config.yaml: demographics`; the manuscript
    mapping left some values, e.g. "British" and "Indian", unmapped). Unlisted values
    become missing. Groups with fewer than 30 episodes are merged into Other,
    counted over analysed episodes (decided 2026-11-23; replaces "set to
    missing"; the manuscript counted over all patients).
    Ethnicity "not stated" / "not known" is missing, as in the manuscript's
    Table 1 and model code (decided 2026-11-24).
    Sex is `female` / `male`; "unspecified" and "not known" become missing.
    `subject` must be unique in the demographics table.
19. A&E (emergency) and outpatient exposures and colonisation are dropped; only
    hospital (inpatient) exposure in the 90 days before admission is kept
    (decided 2026-11-18). Prior-exposure flags are keyed on the spell, not on
    the admission date.
20. Prior ESBL-positive culture counts positive screening samples (e.g. rectal
    swabs) as well as clinical samples, any time before the index admission
    (decided 2026-11-18; the screening exclusion applies to index samples only).
    Samples without a usable susceptibility result are unknown, not positive.
21. Prophylaxis (decided 2026-11-23): window from 2 h before surgery start to
    1 h after surgery **stop** (the manuscript: 1 h after start), with no
    second pass (the manuscript fell back to any IV dose within 6 h of
    surgery). Courses are grouped by each agent's class rather than by
    substring matches on the joined course string; same rules, same order.
22. In-hospital mortality from the discharge method (code 4), not
    `death_date`, which is anonymised to month/year (decided 2026-11-23).
23. Temperature and CRP: closest value within ±48 h of the first culture, in
    the spell and after surgery; descriptive only, removed from the models
    (decided 2026-11-23).
24. Prior antibiotics use calendar days (1–90 days before the admission date)
    instead of floored 24-hour differences from the admission time.
25. Models report odds ratios only: the manuscript code's train/test split,
    AUROC and bootstrap (prediction-model metrics) are dropped (decided
    2026-11-23).
26. Models (decided 2026-11-24): joint Wald screening per variable; reference
    levels must exist (the manuscript code dropped them with
    `errors="ignore"`); constant columns stop the run instead of being dropped
    silently; first episode chosen by date, not by sorting the episode id;
    sparse levels merged into `other` by a fixed rule; the timing variables
    are descriptive only.
27. CHANGE vs 2026-11-17 rule (decided 2026-11-26): an S result for any one
    third-generation cephalosporin (cefotaxime, ceftazidime, ceftriaxone,
    cefpodoxime, cefixime) with no positive evidence is non-ESBL, as in AMR
    surveillance (see section 6). The 2026-11-17 rule becomes a sensitivity
    analysis (not yet built).
28. CHANGE (decided 2026-11-26): new specimen-site group `genital` for vaginal
    samples (`vagina`, `vaginal`, `hvs`, `lvs`; e.g. high/low vaginal swab and
    culture). In the first SDE run these were about 200 of the 437 `other`
    samples, mostly after gynaecological surgery. Episode-site hierarchy:
    after wound, before sputum. Other `other` sites (body-location swabs such
    as `abdomen`, unspecified `swab`, `biliary tract`, endotracheal secretions)
    are unchanged pending a decision.
29. CHANGE (decided 2026-11-26): a cefalexin S result counts as non-ESBL
    evidence. ESBLs hydrolyse first-generation cephalosporins, so an ESBL
    producer is almost always cefalexin-resistant; cefalexin R is NOT
    positive evidence (AmpC, other beta-lactamases and porin changes also
    cause it). Reason: ESBL status was missing for 68% of urine samples,
    consistent with cascade reporting, where 3GCs are reported only after
    first-line resistance (see docs/discussion_notes.md section 1). Lookup
    strings `cephalexin : sensitive` and `cephalexin : susceptible` (the lab spells it cephalexin; checked in the SDE 2026-11-26) added to
    3GCS; check the real cefalexin strings with
    sql/exploration/03_urine_cefalexin.sql (statement 3) and add any others.
    Sensitivity analysis: without this rule (not yet built).
30. Model fits with convergence or separation problems (decided 2026-11-27):
    the primary and first-episode models stop the run; a univariable variable
    or subgroup stratum is reported as not estimable, with no OR or CI.
31. CHANGE (decided 2026-11-27): co-trimoxazole, sulfamethoxazole and
    tetracycline count as antibiotics (the manuscript's suffix rule dropped
    them). Administrations with a status in `antibiotics.not_given_statuses`
    are dropped (list to be filled from the SDE run log).
32. Table S1 shows all 17 Charlson conditions as n (%) (10 were shown as
    median [IQR] of a 0/1 flag).
33. `n_surgeries` counts distinct surgery events, not procedure rows.
34. 3GCS lookup coverage (2026-11-27): cefpodoxime had no susceptible rows and
    cefotaxime / cefixime lacked some word results, because the new code matches
    whole "drug : result" strings (item 11) and the baseline lookup relied on
    matching the result word alone. Added the word results (sensitive,
    susceptible, intermediate, susceptible with optimised dosing) for these three
    drugs; MIC values still need checking against the SDE strings.
