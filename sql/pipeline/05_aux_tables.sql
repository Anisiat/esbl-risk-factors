-- =============================================================================
-- 05_aux_tables.sql   (ports legacy get_aux_tables.sql + pull_healthcare_exposure.sql)
-- PURPOSE   Exposure and covariate tables for infection spells. Each table is
--           restricted to AT_INFECTION_SPELLS (spells with >=1 eligible culture)
--           and keyed by (subject, spell_identifier) so Python joins one-to-many
--           without guessing.
-- READS     AT_INFECTION_SPELLS, STG_EPISODES, ICARE_* tables listed per block,
--           sandbox lookup LOOKUP_PATHOLOGY_INFECTION_AT
-- WRITES    AT_DEMOGRAPHICS            patient
--           AT_PROBLEMS                problem record (all history, no window), with SNOMED code
--           AT_DIAGNOSES               (subject, spell, ICD/SNOMED code), earliest date; all history
--           AT_PRESCRIPTIONS           (subject, medication), earliest order; only the medications
--                                      comorbidicare maps to a Charlson condition; all history
--           AT_ADMINISTRATIONS         spell x drug (non-antibiotic classes); built, not exported
--           AT_ABX_ADMINISTRATIONS     administration, window [admission - lookback, discharge]
--           AT_SURGERY_ABX             surgery x antibiotic administration: in the perioperative window or earlier in the spell
--           AT_TEMPERATURE             observation within the spell
--           AT_PATHOLOGY               result within the spell (infection test codes)
--           AT_CRP                     result within the spell
--           AT_PRIOR_ADMISSIONS        index spell x prior spell overlapping lookback window
--           AT_PRIOR_EMERGENCY         index spell x A&E attendance overlapping lookback window
--           AT_PRIOR_OUTPATIENT        index spell x outpatient appointment in lookback window
--           AT_PROCEDURES_OPCS         spell x coded procedure (validation of TFC specialty only)
-- CHANGES vs legacy
--   * Dates parsed safely (TRY_TO_TIMESTAMP); window ends include the whole
--     discharge day.
--   * Abx pull is bounded by the lookback window (was unbounded).
--   * Pathology LIMIT 500 removed; table-name typo fixed.
--   * Prior admissions exclude the index spell by spell_identifier, not by a
--     date-equality workaround.
--   * The block that legacy called prior_icu is the A&E (emergency) exposure.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;


-- Demographics -----------------------------------------------------------------
-- Legacy columns plus ETHNICITY_DESC / GENDER_DESC (in the data dictionary; kept at the
-- author's request). If the real table names differ, the build fails on this line.
CREATE OR REPLACE TABLE AT_DEMOGRAPHICS AS
SELECT DISTINCT
    d.SUBJECT,
    d.DEATH_DATE,
    d.ETHNICITY,
    d.ETHNICITY_DESC,
    d.GENDER,
    d.GENDER_DESC
FROM ICHT_PROD.ICARE_ICHT.ICARE_DEMOGRAPHIC_ANON d
WHERE d.SUBJECT IN (SELECT DISTINCT SUBJECT FROM AT_INFECTION_SPELLS);


-- Problems (for comorbidities / CCI) ---------------------------------------------
CREATE OR REPLACE TABLE AT_PROBLEMS AS
SELECT
    p.SUBJECT,
    p.ENCNTR_ID,
    p.PROBLEM_CODE,              -- SNOMED CT; comorbidicare requires it
    p.PROBLEM_DESC,
    p.PROBLEM_DT_TM
FROM ICHT_PROD.ICARE_ICHT.ICARE_PROBLEMS_ANON p
WHERE p.SUBJECT IN (SELECT DISTINCT SUBJECT FROM AT_INFECTION_SPELLS);


-- Diagnoses (for comorbidities / CCI) ----------------------------------------------
-- All history of the cohort's patients, because comorbidicare counts only evidence
-- dated before each admission. One row per (subject, spell, code) with the earliest
-- date: one record before the cutoff is enough to set a flag. Column names are from
-- the data dictionary (icare_episodes_diagnosis_anon).
CREATE OR REPLACE TABLE AT_DIAGNOSES AS
SELECT
    d.SUBJECT,
    d.SPELL_IDENTIFIER,
    d.DIAGNOSIS_CODE_ICD,
    d.DIAGNOSIS_CODE_SNOMED,
    MIN(TRY_TO_TIMESTAMP(d.DIAGNOSIS_DATE::STRING)) AS DIAGNOSIS_DATE
FROM ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_DIAGNOSIS_ANON d
WHERE d.SUBJECT IN (SELECT DISTINCT SUBJECT FROM AT_INFECTION_SPELLS)
GROUP BY d.SUBJECT, d.SPELL_IDENTIFIER, d.DIAGNOSIS_CODE_ICD, d.DIAGNOSIS_CODE_SNOMED;


-- Prescriptions (for comorbidities / CCI) ------------------------------------------
-- comorbidicare 0.1.0 (pinned) maps only these 20 medications to a Charlson
-- condition (16 chronic pulmonary disease, 4 uncomplicated diabetes), matching names
-- exactly after lowercasing. List copied from comorbidicare/data/charlson_medication_mapping.csv;
-- update it if the package version changes. One row per (subject, medication) with the
-- earliest order: one order before the cutoff is enough to set a flag.
CREATE OR REPLACE TABLE AT_PRESCRIPTIONS AS
SELECT
    p.SUBJECT,
    LOWER(TRIM(p.MEDICATION_NAME_SHORT))         AS MEDICATION_NAME_SHORT,
    MIN(TRY_TO_TIMESTAMP(p.ORDER_DT_TM::STRING)) AS ORDER_DT_TM
FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_PRESCRIBING_ANON p
WHERE p.SUBJECT IN (SELECT DISTINCT SUBJECT FROM AT_INFECTION_SPELLS)
  AND LOWER(TRIM(p.MEDICATION_NAME_SHORT)) IN (
        'aclidinium',
        'beclometasone',
        'beclometasone-formoterol',
        'beclometasone/formoterol/glycopyrronium',
        'budesonide',
        'budesonide-formoterol',
        'fluticasone',
        'fluticasone-formoterol',
        'fluticasone-salmeterol',
        'fluticasone-vilanterol',
        'fluticasone/umeclidinium/vilanterol',
        'glycopyrronium',
        'glycopyrronium-indacaterol',
        'tiotropium',
        'umeclidinium',
        'umeclidinium-vilanterol',
        'alogliptin',
        'gliclazide',
        'linagliptin',
        'sitagliptin'
  )
GROUP BY p.SUBJECT, LOWER(TRIM(p.MEDICATION_NAME_SHORT));


-- Non-antibiotic drug classes administered during the spell ---------------------
CREATE OR REPLACE TABLE AT_ADMINISTRATIONS AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d,
    a.MEDICATION_NAME_SHORT,
    a.THERAPEUTICAL_CLASS
FROM AT_INFECTION_SPELLS s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
    ON  s.SUBJECT = a.SUBJECT
    AND LOWER(a.THERAPEUTICAL_CLASS) IN (
        'cardiovascular agents',
        'central nervous system agents',
        'coagulation modifiers',
        'gastrointestinal agents',
        'genitourinary tract agents',
        'hormones/hormone modifiers',
        'metabolic agents',
        'miscellaneous agents',
        'nutritional products',
        'none',
        'respiratory agents',
        'topical agents'
    )
    AND TO_DATE(TRY_TO_TIMESTAMP(a.ADMINISTRATION_DT::STRING)) BETWEEN s.admission_d AND s.discharge_d;


-- Antibiotic administrations: [admission - lookback, discharge] ----------------
-- Covers prior antibiotics (before admission) and perioperative prophylaxis
-- (during the spell). Python applies the exact exposure / prophylaxis windows.
CREATE OR REPLACE TABLE AT_ABX_ADMINISTRATIONS AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d,
    s.discharge_d,
    a.ENCNTR_ID,
    a.ADMINISTRATION_DT,
    TRY_TO_TIMESTAMP(a.ADMINISTRATION_DT::STRING) AS administration_ts,
    a.MEDICATION_NAME_SHORT,
    a.THERAPEUTICAL_CLASS,
    a.ROUTE,
    a.ADMINISTRATION_STATUS
FROM AT_INFECTION_SPELLS s
CROSS JOIN AT_PARAMS p
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
    ON  s.SUBJECT = a.SUBJECT
    AND LOWER(a.THERAPEUTICAL_CLASS) = 'anti-infectives'
    AND TO_DATE(TRY_TO_TIMESTAMP(a.ADMINISTRATION_DT::STRING))
        BETWEEN DATEADD(day, -1 * p.lookback_days, s.admission_d) AND s.discharge_d;


-- Antibiotics around each surgery ------------------------------------------------------------
-- One row per (surgery, antibiotic administration) from admission up to the end of the window:
--   in_window     = 1: from prophylaxis_hours_before_start before surgery start to
--                      prophylaxis_hours_after_stop after surgery stop (the prophylaxis course)
--   before_window = 1: earlier in the same spell (the patient was already on antibiotics)
-- Python (esbl_rf) chooses the index surgery per episode by surgery_event_id, keeps antibacterial
-- drugs, applies the route and status rules, and groups the course. Antibiotics before admission
-- are in AT_ABX_ADMINISTRATIONS (past antibiotics), not here.
CREATE OR REPLACE TABLE AT_SURGERY_ABX AS
SELECT DISTINCT
    c.SUBJECT,
    c.SPELL_IDENTIFIER,
    c.surgery_event_id,
    c.surgery_start_ts,
    c.surgery_stop_ts,
    a.MEDICATION_NAME_SHORT,
    a.ROUTE,
    a.ADMINISTRATION_STATUS,
    a.administration_ts,
    IFF(a.administration_ts >= DATEADD(hour, -1 * p.prophylaxis_hours_before_start, c.surgery_start_ts)
        AND a.administration_ts <= DATEADD(hour, p.prophylaxis_hours_after_stop, c.surgery_stop_ts), 1, 0) AS in_window,
    IFF(a.administration_ts < DATEADD(hour, -1 * p.prophylaxis_hours_before_start, c.surgery_start_ts), 1, 0) AS before_window
FROM AT_ELECTIVE_SURGERY_COHORT c
CROSS JOIN AT_PARAMS p
INNER JOIN AT_ABX_ADMINISTRATIONS a
    ON  c.SUBJECT = a.SUBJECT
    AND c.SPELL_IDENTIFIER = a.SPELL_IDENTIFIER
    AND a.administration_ts >= c.admission_ts
    AND a.administration_ts <= DATEADD(hour, p.prophylaxis_hours_after_stop, c.surgery_stop_ts);

-- CHECK: how many surgeries have an antibiotic in the window / earlier in the spell (aggregate, for you to read).
SELECT COUNT(*)                                            AS n_rows,
       COUNT(DISTINCT surgery_event_id)                    AS n_surgeries_with_abx,
       COUNT(DISTINCT IFF(in_window = 1, surgery_event_id, NULL))     AS n_surgeries_with_window_dose,
       COUNT(DISTINCT IFF(before_window = 1, surgery_event_id, NULL)) AS n_surgeries_already_on_abx,
       (SELECT COUNT(DISTINCT surgery_event_id) FROM AT_ELECTIVE_SURGERY_COHORT s
         WHERE s.SPELL_IDENTIFIER IN (SELECT SPELL_IDENTIFIER FROM AT_INFECTION_SPELLS)) AS n_surgeries_in_infection_spells
FROM AT_SURGERY_ABX;


-- Temperature -------------------------------------------------------------------
CREATE OR REPLACE TABLE AT_TEMPERATURE AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d,
    v.OBSERVATION_PERFORMED_DT                              AS temp_performed_dt,
    TRY_TO_TIMESTAMP(v.OBSERVATION_PERFORMED_DT::STRING)    AS temp_performed_ts,
    v.OBSERVATION_RESULT_CLEAN                              AS temperature
FROM AT_INFECTION_SPELLS s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_VITAL_SIGNS_ANON v
    ON  s.SUBJECT = v.SUBJECT
    AND LOWER(v.OBSERVATION_NAME) LIKE '%temperature%'
    AND TO_DATE(TRY_TO_TIMESTAMP(v.OBSERVATION_PERFORMED_DT::STRING)) BETWEEN s.admission_d AND s.discharge_d;


-- Pathology (infection-related test codes from the sandbox lookup) ---------------
CREATE OR REPLACE TABLE AT_PATHOLOGY AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d,
    p.ORDER_CODE,
    p.TEST_CODE,
    p.RESULT_AVAILABLE_DT,
    p.RESULT_CLEANED,
    p.RESULT_LOWER_RANGE,
    p.RESULT_UPPER_RANGE,
    p.SAMPLE_COLLECTED_DT,
    TRY_TO_TIMESTAMP(p.SAMPLE_COLLECTED_DT::STRING) AS sample_collected_ts,
    p.TEST_RESULT_UNIT
FROM AT_INFECTION_SPELLS s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_PATHOLOGY_BLOOD_ANON p
    ON  s.SUBJECT = p.SUBJECT
    AND TO_DATE(TRY_TO_TIMESTAMP(p.SAMPLE_COLLECTED_DT::STRING)) BETWEEN s.admission_d AND s.discharge_d
WHERE p.TEST_CODE IN (
    SELECT DISTINCT TEST_CODE
    FROM ICHT_SANDBOX_PROD.SMTPATH_25046.LOOKUP_PATHOLOGY_INFECTION_AT
);


-- CRP ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE AT_CRP AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d,
    p.RESULT_AVAILABLE_DT,
    p.RESULT_CLEANED AS crp,
    p.SAMPLE_COLLECTED_DT,
    TRY_TO_TIMESTAMP(p.SAMPLE_COLLECTED_DT::STRING) AS sample_collected_ts
FROM AT_INFECTION_SPELLS s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_PATHOLOGY_BLOOD_ANON p
    ON  s.SUBJECT = p.SUBJECT
    AND TO_DATE(TRY_TO_TIMESTAMP(p.SAMPLE_COLLECTED_DT::STRING)) BETWEEN s.admission_d AND s.discharge_d
WHERE LOWER(p.TEST_CODE) LIKE '%crp%';


-- Prior inpatient exposure ---------------------------------------------------------
-- Any other spell overlapping [index admission - lookback, index admission).
CREATE OR REPLACE TABLE AT_PRIOR_ADMISSIONS AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d                AS index_admission_d,
    a.SPELL_IDENTIFIER           AS prior_spell_identifier,
    a.admission_d                AS prior_admission_d,
    a.discharge_d                AS prior_discharge_d,
    a.DISCHARGE_DESTINATION      AS prior_discharge_destination
FROM AT_INFECTION_SPELLS s
CROSS JOIN AT_PARAMS p
INNER JOIN STG_EPISODES a
    ON  s.SUBJECT = a.SUBJECT
    AND a.SPELL_IDENTIFIER <> s.SPELL_IDENTIFIER
    AND a.admission_d < s.admission_d
    AND COALESCE(a.discharge_d, a.admission_d) >= DATEADD(day, -1 * p.lookback_days, s.admission_d);


-- Prior emergency (A&E) exposure --------------------------------------------------------
-- TODO analysis_plan says "attendance in [admission - 90 d, admission)"; legacy code
-- used overlap with the window (departure >= start of window). Legacy kept here.
CREATE OR REPLACE TABLE AT_PRIOR_EMERGENCY AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d                                            AS index_admission_d,
    TO_DATE(TRY_TO_TIMESTAMP(i.ARRIVAL_DATE::STRING))        AS arrival_d,
    TO_DATE(TRY_TO_TIMESTAMP(i.DEPARTURE_DATE::STRING))      AS departure_d
FROM AT_INFECTION_SPELLS s
CROSS JOIN AT_PARAMS p
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EMERGENCY_ANON i
    ON  s.SUBJECT = i.SUBJECT
    AND TO_DATE(TRY_TO_TIMESTAMP(i.ARRIVAL_DATE::STRING)) < s.admission_d
    AND COALESCE(
            TO_DATE(TRY_TO_TIMESTAMP(i.DEPARTURE_DATE::STRING)),
            TO_DATE(TRY_TO_TIMESTAMP(i.ARRIVAL_DATE::STRING))
        ) >= DATEADD(day, -1 * p.lookback_days, s.admission_d);


-- Prior outpatient exposure (not used in the analysis plan; kept from legacy) ----------
CREATE OR REPLACE TABLE AT_PRIOR_OUTPATIENT AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    s.admission_d                                              AS index_admission_d,
    TO_DATE(TRY_TO_TIMESTAMP(o.APPOINTMENT_DATE::STRING))      AS appointment_d
FROM AT_INFECTION_SPELLS s
CROSS JOIN AT_PARAMS p
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_OUTPATIENT_ANON o
    ON  s.SUBJECT = o.SUBJECT
    AND TO_DATE(TRY_TO_TIMESTAMP(o.APPOINTMENT_DATE::STRING)) < s.admission_d
    AND TO_DATE(TRY_TO_TIMESTAMP(o.APPOINTMENT_DATE::STRING)) >= DATEADD(day, -1 * p.lookback_days, s.admission_d);


-- Coded procedures (OPCS) -----------------------------------------------------------
-- Validation only: lets us cross-tabulate TFC specialty against OPCS procedure
-- chapter. Not an analysis variable (that would change a study definition).
-- Column names are from the data dictionary; this table was not used in legacy SQL.
CREATE OR REPLACE TABLE AT_PROCEDURES_OPCS AS
SELECT DISTINCT
    s.SUBJECT,
    s.SPELL_IDENTIFIER,
    p.EPISODE_IDENTIFIER,
    p.ORDER_NO_OF_EPISODE,
    p.PROCEDURE_SEQ_NBR,
    p.PROCEDURE_CODE_OPCS,
    p.PROCEDURE_NAME_OPCS,
    p.PROCEDURE_DATE,
    TRY_TO_TIMESTAMP(p.PROCEDURE_DATE::STRING) AS procedure_ts
FROM AT_INFECTION_SPELLS s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_PROCEDURES_ANON p
    ON  s.SUBJECT = p.SUBJECT
    AND s.SPELL_IDENTIFIER = p.SPELL_IDENTIFIER;


-- CHECK: every aux table should have rows (EMPTY = investigate; some, e.g. prior
-- emergency, can be small but should not be zero in a multi-year cohort).
SELECT 'AT_INFECTION_SPELLS (reference)' AS table_name, COUNT(*) AS n_rows, COUNT(DISTINCT SUBJECT) AS n_subjects, IFF(COUNT(*) = 0, 'EMPTY', 'OK') AS check_result FROM AT_INFECTION_SPELLS
UNION ALL SELECT 'AT_DEMOGRAPHICS',        COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_DEMOGRAPHICS
UNION ALL SELECT 'AT_PROBLEMS',            COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PROBLEMS
UNION ALL SELECT 'AT_DIAGNOSES',           COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_DIAGNOSES
UNION ALL SELECT 'AT_PRESCRIPTIONS',       COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PRESCRIPTIONS
UNION ALL SELECT 'AT_ADMINISTRATIONS',     COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_ADMINISTRATIONS
UNION ALL SELECT 'AT_ABX_ADMINISTRATIONS', COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_ABX_ADMINISTRATIONS
UNION ALL SELECT 'AT_TEMPERATURE',         COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_TEMPERATURE
UNION ALL SELECT 'AT_PATHOLOGY',           COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PATHOLOGY
UNION ALL SELECT 'AT_CRP',                 COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_CRP
UNION ALL SELECT 'AT_PRIOR_ADMISSIONS',    COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PRIOR_ADMISSIONS
UNION ALL SELECT 'AT_PRIOR_EMERGENCY',     COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PRIOR_EMERGENCY
UNION ALL SELECT 'AT_PRIOR_OUTPATIENT',    COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PRIOR_OUTPATIENT
UNION ALL SELECT 'AT_PROCEDURES_OPCS',     COUNT(*), COUNT(DISTINCT SUBJECT), IFF(COUNT(*) = 0, 'EMPTY', 'OK') FROM AT_PROCEDURES_OPCS;
