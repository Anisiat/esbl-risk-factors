-- =============================================================================
-- 10_exports.sql
-- PURPOSE   The final SELECTs whose results are downloaded as CSV and placed in
--           data/raw/ for the Python pipeline.
-- HOW       Run 00-09 first (see 00_params.sql). Then run EACH statement below on its own
--           (a worksheet only shows the result of the last statement it ran) and
--           use "Download results" -> CSV. Name the file exactly as in the
--           "OUTPUT CSV" line above each statement.
-- CHECK     Export qa_checks.csv and qa_build_log.csv last; Python compares its
--           own row counts against qa_build_log.csv.
-- Grain is stated for each file; Python must assert key uniqueness after joins.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;


-- OUTPUT CSV: qa_checks.csv           grain: one row per check
SELECT * FROM AT_QA_CHECKS ORDER BY IFF(status = 'FAIL', 0, IFF(status = 'WARN', 1, 2)), layer, check_name;

-- OUTPUT CSV: qa_flowchart_sql.csv    grain: one row per flowchart step
SELECT * FROM AT_QA_FLOWCHART ORDER BY step;

-- OUTPUT CSV: qa_build_log.csv        grain: one row per logged item
SELECT * FROM AT_BUILD_LOG;

-- OUTPUT CSV: surgery_cohort.csv      grain: (subject, spell, surgery start, stop, procedure)
-- Only surgeries in stays with >=1 eligible culture (AT_INFECTION_SPELLS). Counts for the
-- full elective cohort (flowchart steps 1-2) are in qa_flowchart_sql.csv.
SELECT c.*
FROM AT_ELECTIVE_SURGERY_COHORT c
INNER JOIN AT_INFECTION_SPELLS s
    ON  c.SUBJECT = s.SUBJECT
    AND c.SPELL_IDENTIFIER = s.SPELL_IDENTIFIER;

-- OUTPUT CSV: spells.csv              grain: (subject, spell) with >=1 eligible culture
SELECT * FROM AT_INFECTION_SPELLS;

-- OUTPUT CSV: spell_microbiology.csv  grain: (spell, lab_test_id, organism_bug)
SELECT * FROM AT_ELECTIVE_SPELL_MICROBIOLOGY;

-- OUTPUT CSV: microbiology_ekp.csv    grain: (lab_test_id, organism_bug), all time, for prior-ESBL history
SELECT * FROM AT_MICROBIOLOGY_EKP;

-- OUTPUT CSV: microbiology_results.csv  grain: (subject, lab_test_id, organism_bug, sensitivity string)
SELECT * FROM AT_MICROBIOLOGY_RESULTS;

-- OUTPUT CSV: demographics.csv        grain: patient
SELECT * FROM AT_DEMOGRAPHICS;

-- OUTPUT CSV: problems.csv            grain: problem record (with SNOMED problem_code)
SELECT * FROM AT_PROBLEMS;

-- OUTPUT CSV: diagnoses.csv           grain: (subject, spell, ICD code, SNOMED code), earliest date
SELECT * FROM AT_DIAGNOSES;

-- OUTPUT CSV: prescriptions.csv       grain: (subject, medication), earliest order; comorbidicare medications only
SELECT * FROM AT_PRESCRIPTIONS;

-- OUTPUT CSV: abx_administrations.csv grain: administration (within window)
SELECT * FROM AT_ABX_ADMINISTRATIONS;

-- OUTPUT CSV: temperature.csv         grain: observation within spell
SELECT * FROM AT_TEMPERATURE;

-- OUTPUT CSV: crp.csv                 grain: result within spell
SELECT * FROM AT_CRP;

-- OUTPUT CSV: prior_inpatient_exposure.csv    grain: (index spell, prior spell)
SELECT * FROM AT_PRIOR_ADMISSIONS;

-- OUTPUT CSV: prior_emergency_exposure.csv    grain: (index spell, A&E attendance)
SELECT * FROM AT_PRIOR_EMERGENCY;

-- OUTPUT CSV: prior_outpatient_exposure.csv   grain: (index spell, appointment)
SELECT * FROM AT_PRIOR_OUTPATIENT;
