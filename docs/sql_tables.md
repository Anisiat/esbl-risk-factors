# SQL pipeline tables

What each Snowflake table in `sql/pipeline/` holds, which CSV it becomes, and what
Python uses it for. Run the files in number order (00, 01, 02, 03, 04, 05, 09), then
export the CSVs listed in `10_exports.sql` into `data/raw/`.

"Grain" is what one row represents. "Planned" means the Python step is not written yet.

## Built and used inside Snowflake only

| Table | File | Grain | Contents | Used for |
|---|---|---|---|---|
| `AT_PARAMS` | 00 | one row | Study dates, surgery anchor (start), minimum age, 90-day lookback, git hash | Every later SQL file reads its parameters from here |
| `STG_SURGERY` | 01 | procedure row | Raw surgery table plus typed timestamps | Dates parsed once, so later files never parse strings |
| `STG_EPISODES` | 01 | consultant episode (several per spell) | Raw episodes table plus typed dates, for patients in the surgery table | Surgery-to-episode linkage; prior admissions |
| `AT_SURGERY_LINKED` | 02 | surgery procedure in a spell | Adult elective admissions with at least one surgery in the window, linked to the episode that contains the surgery | Flow-diagram step 1 |
| `AT_ELECTIVE_SURGERY_COHORT` | 02 | surgery procedure in a spell | The above without emergency procedures | Flow-diagram step 2; exported in restricted form as `surgery_cohort.csv` |
| `AT_ELECTIVE_SPELLS` | 02 | spell | Spell-level fields (dates, admission method, age, IMD decile, discharge details, number of surgery events) | Basis for the spell-level tables below |

## Exported as CSV

| CSV (table) | Grain | Contents | Used downstream for |
|---|---|---|---|
| `qa_checks.csv` (`AT_QA_CHECKS`) | check | PASS / WARN / FAIL for dates, key uniqueness, empty tables | Read first after every SQL run; not analysed |
| `qa_flowchart_sql.csv` (`AT_QA_FLOWCHART`) | flow-diagram step | Counts for the first flow-diagram steps (elective admissions, after emergency exclusion, admissions with an eligible culture) | Top boxes of the flow diagram (planned) |
| `qa_build_log.csv` (`AT_BUILD_LOG`) | logged item | Parameters, git hash, row counts per table | Python compares its row counts against it; run log |
| `surgery_cohort.csv` (`AT_ELECTIVE_SURGERY_COHORT`, spells with a culture only) | surgery in a spell | Surgery start/stop, procedure, surgical area, containing episode | Attribute each sample to its most recent preceding surgery; surgery duration, specialty, number of surgeries (planned) |
| `spells.csv` (`AT_INFECTION_SPELLS`) | spell with at least one eligible culture | Admission and discharge dates, admission method, age, IMD decile, discharge details | Age and IMD; length of stay; 48 h rule; in-hospital mortality; the spell list everything else is keyed on (planned) |
| `spell_microbiology.csv` (`AT_ELECTIVE_SPELL_MICROBIOLOGY`) | (spell, lab test, organism) | Eligible-organism samples collected from admission until the later of discharge and 30 days after the spell's last surgery (`collected_during_admission` marks the in-hospital ones), flags for whether ESBL status can be judged (no susceptibility columns; the result strings are in `microbiology_results.csv`) | Sample eligibility filters, screening exclusion, ESBL labelling, infection episodes (planned) |
| `microbiology_ekp.csv` (`AT_MICROBIOLOGY_EKP`) | (lab test, organism) | Same data over all time, for cohort patients; includes screening samples. Labelled once in Python (all samples), then the labels are joined to `spell_microbiology.csv` | ESBL labelling, prior ESBL-positive culture |
| `microbiology_results.csv` (`AT_MICROBIOLOGY_RESULTS`) | (subject, lab test, organism, result string) | Raw susceptibility strings such as `ceftriaxone : 4` for cefotaxime, ceftazidime, ceftriaxone, cefpodoxime, cefixime and the ESBL marker; unsplit | ESBL labelling against `lookups/3GCR.csv` and `3GCS.csv` |
| `demographics.csv` (`AT_DEMOGRAPHICS`) | patient | Death date, ethnicity and description, gender and description | Sex, ethnicity, in-hospital mortality (planned) |
| `problems.csv` (`AT_PROBLEMS`) | problem record | Problem description, SNOMED `problem_code`, date; all history | comorbidicare evidence for the CCI (planned) |
| `diagnoses.csv` (`AT_DIAGNOSES`) | (subject, spell, ICD code, SNOMED code) | Earliest diagnosis date per code; all history | comorbidicare evidence for the CCI (planned) |
| `prescriptions.csv` (`AT_PRESCRIPTIONS`) | (subject, medication) | Earliest order date for the 20 medications comorbidicare maps (inhalers, some diabetes drugs) | comorbidicare evidence for chronic pulmonary disease and uncomplicated diabetes (planned) |
| `abx_administrations.csv` (`AT_ABX_ADMINISTRATIONS`) | antibiotic administration | Drug, class, route, time; from 90 days before admission to discharge | Prior antibiotic exposure and perioperative prophylaxis group (planned) |
| `surgery_abx.csv` (`AT_SURGERY_ABX`) | (surgery, antibiotic administration) | Antibiotics from admission to 1 h after surgery stop, flagged `in_window` (2 h before start to 1 h after stop) or `before_window` (already on antibiotics) | Prophylaxis group and the "already on antibiotics" category (planned) |
| `temperature.csv` (`AT_TEMPERATURE`) | observation in the spell | Temperature and time | Temperature covariate (planned) |
| `crp.csv` (`AT_CRP`) | result in the spell | CRP value and time | CRP covariate (planned) |
| `prior_inpatient_exposure.csv` (`AT_PRIOR_ADMISSIONS`) | (index spell, prior spell) | Earlier stays overlapping the 90 days before admission | Prior hospitalisation (inpatient only) (planned) |

## Built but not exported

| Table | Why not exported |
|---|---|
| `AT_ADMINISTRATIONS` | Non-antibiotic drug administrations during the spell; the baseline never used an equivalent file, and comorbidicare needs prescribing orders instead (`prescriptions.csv`) |
| `AT_PATHOLOGY` | Not used by the baseline analysis |
| `AT_PROCEDURES_OPCS` | Validation of the specialty variable only, not an analysis input |

These three could be removed from `05_aux_tables.sql` to save compute; they are left in so
they can be exported later without re-running the pipeline.


Not exported: `AT_PRIOR_EMERGENCY` (A&E attendances) and `AT_PRIOR_OUTPATIENT` stay in the SDE but are not used (decided 2026-11-18).
