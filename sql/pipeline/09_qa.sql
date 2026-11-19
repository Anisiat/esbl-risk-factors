-- =============================================================================
-- 09_qa.sql
-- PURPOSE   Automatic checks and a counts report, built from the tables above.
-- WRITES    AT_QA_CHECKS     one row per check: PASS / WARN / FAIL / INFO
--           AT_QA_FLOWCHART  cohort counts for flowchart steps 1-3 (SQL part)
--           AT_BUILD_LOG     parameters, git hash, row counts, date range
-- READ THIS FIRST after every build: SELECT * FROM AT_QA_CHECKS WHERE status IN ('FAIL','WARN');
-- severity FAIL = something is wrong, do not use the data. WARN = look, may be fine.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

CREATE OR REPLACE TABLE AT_QA_CHECKS AS
WITH checks AS (

    -- ---- staging: dates that did not parse ----------------------------------
    SELECT 'stg_surgery_start_unparsed' AS check_name, 'staging' AS layer, 'WARN' AS severity,
           COUNT(*) AS observed, '0' AS expected, COUNT(*) = 0 AS ok
    FROM STG_SURGERY
    WHERE SURGERY_START_DT IS NOT NULL AND LOWER(TRIM(SURGERY_START_DT::STRING)) <> 'none' AND surgery_start_ts IS NULL
    UNION ALL
    SELECT 'stg_surgery_stop_unparsed', 'staging', 'WARN', COUNT(*), '0', COUNT(*) = 0
    FROM STG_SURGERY
    WHERE SURGERY_STOP_DT IS NOT NULL AND LOWER(TRIM(SURGERY_STOP_DT::STRING)) <> 'none' AND surgery_stop_ts IS NULL
    UNION ALL
    SELECT 'stg_episodes_admission_unparsed', 'staging', 'WARN', COUNT(*), '0', COUNT(*) = 0
    FROM STG_EPISODES
    WHERE ADMISSION_DATE IS NOT NULL AND LOWER(TRIM(ADMISSION_DATE::STRING)) <> 'none' AND admission_d IS NULL
    UNION ALL
    SELECT 'stg_episodes_discharge_unparsed', 'staging', 'WARN', COUNT(*), '0', COUNT(*) = 0
    FROM STG_EPISODES
    WHERE DISCHARGE_DATE IS NOT NULL AND LOWER(TRIM(DISCHARGE_DATE::STRING)) <> 'none' AND discharge_d IS NULL
    UNION ALL
    SELECT 'stg_episodes_discharge_none_or_null', 'staging', 'INFO', COUNT(*), 'info', TRUE
    FROM STG_EPISODES WHERE discharge_d IS NULL

    -- ---- cohort -----------------------------------------------------------------
    UNION ALL
    SELECT 'surgery_linked_empty', 'cohort', 'FAIL', COUNT(*), '> 0', COUNT(*) > 0
    FROM AT_SURGERY_LINKED
    UNION ALL
    SELECT 'surgery_linked_key_duplicates', 'cohort', 'FAIL',
           COUNT(*) - COUNT(DISTINCT HASH(SUBJECT, SPELL_IDENTIFIER, surgery_start_ts, surgery_stop_ts, PROCEDURE_DESC)),
           '0',
           COUNT(*) = COUNT(DISTINCT HASH(SUBJECT, SPELL_IDENTIFIER, surgery_start_ts, surgery_stop_ts, PROCEDURE_DESC))
    FROM AT_SURGERY_LINKED
    UNION ALL
    SELECT 'surgery_linked_no_containing_episode', 'cohort', 'WARN', COUNT_IF(episode_contains_surgery = 0), '0',
           COUNT_IF(episode_contains_surgery = 0) = 0
    FROM AT_SURGERY_LINKED
    UNION ALL
    SELECT 'surgery_linked_ambiguous_episode', 'cohort', 'WARN', COUNT_IF(n_episodes_containing_surgery > 1), '0',
           COUNT_IF(n_episodes_containing_surgery > 1) = 0
    FROM AT_SURGERY_LINKED
    UNION ALL
    SELECT 'surgery_linked_unresolved_after_time_tiebreak', 'cohort', 'WARN',
           COUNT_IF(n_episodes_containing_surgery > 1 AND n_episodes_containing_surgery_time <> 1), '0',
           COUNT_IF(n_episodes_containing_surgery > 1 AND n_episodes_containing_surgery_time <> 1) = 0
    FROM AT_SURGERY_LINKED
    UNION ALL
    -- Surgery has no spell id, so it is linked to spells by subject + date window.
    -- The same surgery matching two spells would mean overlapping spells.
    SELECT 'surgery_linked_in_multiple_spells', 'cohort', 'WARN', COUNT(*), '0', COUNT(*) = 0
    FROM (
        SELECT SUBJECT, surgery_start_ts, surgery_stop_ts, PROCEDURE_DESC
        FROM AT_SURGERY_LINKED
        GROUP BY SUBJECT, surgery_start_ts, surgery_stop_ts, PROCEDURE_DESC
        HAVING COUNT(DISTINCT SPELL_IDENTIFIER) > 1
    )
    UNION ALL
    SELECT 'cohort_empty', 'cohort', 'FAIL', COUNT(*), '> 0', COUNT(*) > 0
    FROM AT_ELECTIVE_SURGERY_COHORT
    UNION ALL
    SELECT 'cohort_spells_with_conflicting_dates', 'cohort', 'FAIL', COUNT(*), '0', COUNT(*) = 0
    FROM (
        SELECT SUBJECT, SPELL_IDENTIFIER
        FROM AT_ELECTIVE_SURGERY_COHORT
        GROUP BY SUBJECT, SPELL_IDENTIFIER
        HAVING COUNT(DISTINCT admission_d) > 1 OR COUNT(DISTINCT discharge_d) > 1
    )
    UNION ALL
    SELECT 'spells_key_duplicates', 'cohort', 'FAIL',
           COUNT(*) - COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING), '0',
           COUNT(*) = COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING)
    FROM AT_ELECTIVE_SPELLS

    -- ---- microbiology -------------------------------------------------------------
    UNION ALL
    SELECT 'micro_empty', 'microbiology', 'FAIL', COUNT(*), '> 0', COUNT(*) > 0
    FROM AT_MICROBIOLOGY_EKP
    UNION ALL
    SELECT 'micro_key_duplicates', 'microbiology', 'FAIL',
           COUNT(*) - COUNT(DISTINCT LAB_TEST_ID::STRING || '|' || ORGANISM_BUG), '0',
           COUNT(*) = COUNT(DISTINCT LAB_TEST_ID::STRING || '|' || ORGANISM_BUG)
    FROM AT_MICROBIOLOGY_EKP
    UNION ALL
    SELECT 'micro_collect_time_unparsed', 'microbiology', 'WARN', COUNT(*), '0', COUNT(*) = 0
    FROM AT_MICROBIOLOGY_EKP WHERE collect_ts IS NULL
    UNION ALL
    SELECT 'micro_rows_esbl_statement_organism', 'microbiology', 'INFO', COUNT(*), 'info', TRUE
    FROM AT_MICROBIOLOGY_EKP WHERE esbl_statement = 1
    UNION ALL
    SELECT 'micro_rows_esbl_statement_lab', 'microbiology', 'INFO', COUNT(*), 'info', TRUE
    FROM AT_MICROBIOLOGY_EKP WHERE esbl_statement_lab = 1
    UNION ALL
    SELECT 'micro_rows_no_ast_info_at_all', 'microbiology', 'INFO', COUNT(*), 'info', TRUE
    FROM AT_MICROBIOLOGY_EKP
    WHERE has_ast_info = 0 AND esbl_statement = 0 AND esbl_statement_lab = 0
    UNION ALL
    SELECT 'spell_micro_empty', 'microbiology', 'FAIL', COUNT(*), '> 0', COUNT(*) > 0
    FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
    UNION ALL
    -- A sample falling inside two spells of one patient would be counted twice.
    SELECT 'spell_micro_sample_in_multiple_spells', 'microbiology', 'FAIL',
           COUNT(*) - COUNT(DISTINCT LAB_TEST_ID::STRING || '|' || ORGANISM_BUG), '0',
           COUNT(*) = COUNT(DISTINCT LAB_TEST_ID::STRING || '|' || ORGANISM_BUG)
    FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
    UNION ALL
    -- Samples linked after discharge (within the post-surgery window): informational.
    SELECT 'spell_micro_rows_after_discharge', 'microbiology', 'INFO', COUNT(*), 'info', TRUE
    FROM AT_ELECTIVE_SPELL_MICROBIOLOGY WHERE collected_during_admission = 0

    -- ---- aux tables are not empty ----------------------------------------------------
    UNION ALL SELECT 'aux_demographics_empty',    'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_DEMOGRAPHICS
    UNION ALL SELECT 'aux_problems_empty',        'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_PROBLEMS
    UNION ALL SELECT 'aux_diagnoses_empty',       'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_DIAGNOSES
    UNION ALL SELECT 'aux_prescriptions_empty',   'aux', 'WARN', COUNT(*), '> 0 (0 = no medication name matched comorbidicare''s list; check MEDICATION_NAME_SHORT values)', COUNT(*) > 0 FROM AT_PRESCRIPTIONS
    UNION ALL SELECT 'aux_administrations_empty', 'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_ADMINISTRATIONS
    UNION ALL SELECT 'aux_abx_empty',             'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_ABX_ADMINISTRATIONS
    UNION ALL SELECT 'aux_surgery_abx_empty',     'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_SURGERY_ABX
    UNION ALL SELECT 'aux_temperature_empty',     'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_TEMPERATURE
    UNION ALL SELECT 'aux_pathology_empty',       'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_PATHOLOGY
    UNION ALL SELECT 'aux_crp_empty',             'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_CRP
    UNION ALL SELECT 'aux_prior_admissions_empty','aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_PRIOR_ADMISSIONS
    UNION ALL SELECT 'aux_prior_emergency_empty', 'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_PRIOR_EMERGENCY
    UNION ALL SELECT 'aux_procedures_opcs_empty', 'aux', 'WARN', COUNT(*), '> 0', COUNT(*) > 0 FROM AT_PROCEDURES_OPCS
)
SELECT
    check_name,
    layer,
    severity,
    observed,
    expected,
    CASE WHEN severity = 'INFO' THEN 'INFO' WHEN ok THEN 'PASS' ELSE severity END AS status,
    CURRENT_TIMESTAMP() AS checked_at
FROM checks;


-- Flowchart steps that SQL can answer. Python continues from step 4
-- (organisms are already restricted here; sample eligibility, episodes and ESBL
-- status are derived in Python).
CREATE OR REPLACE TABLE AT_QA_FLOWCHART AS
SELECT 1 AS step,
       'Adult elective admissions with >=1 surgery in window (emergency flag not yet applied)' AS description,
       COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING) AS n_admissions,
       COUNT(DISTINCT SUBJECT)           AS n_patients,
       COUNT(DISTINCT surgery_event_id)  AS n_surgery_events,
       NULL::NUMBER                      AS n_cultures
FROM AT_SURGERY_LINKED
UNION ALL
SELECT 2, 'After excluding surgery events with an emergency procedure',
       COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING),
       COUNT(DISTINCT SUBJECT),
       COUNT(DISTINCT surgery_event_id),
       NULL
FROM AT_ELECTIVE_SURGERY_COHORT
UNION ALL
SELECT 3, 'Admissions with >=1 culture of an eligible organism during the admission or within the post-surgery window',
       COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING),
       COUNT(DISTINCT SUBJECT),
       NULL,
       COUNT(*)
FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
UNION ALL
SELECT 31, '  of which culture has AST info or an ESBL statement (informational)',
       COUNT(DISTINCT SUBJECT::STRING || '|' || SPELL_IDENTIFIER::STRING),
       COUNT(DISTINCT SUBJECT),
       NULL,
       COUNT(*)
FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
WHERE has_ast_info = 1 OR esbl_statement = 1 OR esbl_statement_lab = 1
ORDER BY step;


CREATE OR REPLACE TABLE AT_BUILD_LOG AS
SELECT 'param' AS item, 'git_hash' AS item_name, git_hash AS item_value FROM AT_PARAMS
UNION ALL SELECT 'param', 'study_start',    TO_VARCHAR(study_start)    FROM AT_PARAMS
UNION ALL SELECT 'param', 'study_end',      TO_VARCHAR(study_end)      FROM AT_PARAMS
UNION ALL SELECT 'param', 'surgery_anchor', surgery_anchor             FROM AT_PARAMS
UNION ALL SELECT 'param', 'min_age',        TO_VARCHAR(min_age)        FROM AT_PARAMS
UNION ALL SELECT 'param', 'lookback_days',  TO_VARCHAR(lookback_days)  FROM AT_PARAMS
UNION ALL SELECT 'param', 'max_days_after_surgery', TO_VARCHAR(max_days_after_surgery) FROM AT_PARAMS
UNION ALL SELECT 'build', 'built_at',       CURRENT_TIMESTAMP()::STRING
UNION ALL SELECT 'range', 'max_surgery_stop_in_source', MAX(surgery_stop_ts)::STRING FROM STG_SURGERY
UNION ALL SELECT 'range', 'max_admission_in_source',    MAX(admission_d)::STRING FROM STG_EPISODES
UNION ALL SELECT 'rows', 'STG_SURGERY',                     COUNT(*)::STRING FROM STG_SURGERY
UNION ALL SELECT 'rows', 'STG_EPISODES',                    COUNT(*)::STRING FROM STG_EPISODES
UNION ALL SELECT 'rows', 'AT_SURGERY_LINKED',               COUNT(*)::STRING FROM AT_SURGERY_LINKED
UNION ALL SELECT 'rows', 'AT_ELECTIVE_SURGERY_COHORT',      COUNT(*)::STRING FROM AT_ELECTIVE_SURGERY_COHORT
UNION ALL SELECT 'rows', 'AT_ELECTIVE_SPELLS',              COUNT(*)::STRING FROM AT_ELECTIVE_SPELLS
UNION ALL SELECT 'rows', 'AT_MICROBIOLOGY_EKP',             COUNT(*)::STRING FROM AT_MICROBIOLOGY_EKP
UNION ALL SELECT 'rows', 'AT_MICROBIOLOGY_RESULTS',         COUNT(*)::STRING FROM AT_MICROBIOLOGY_RESULTS
UNION ALL SELECT 'rows', 'AT_ELECTIVE_SPELL_MICROBIOLOGY',  COUNT(*)::STRING FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
UNION ALL SELECT 'rows', 'AT_INFECTION_SPELLS',             COUNT(*)::STRING FROM AT_INFECTION_SPELLS
UNION ALL SELECT 'rows', 'AT_DEMOGRAPHICS',                 COUNT(*)::STRING FROM AT_DEMOGRAPHICS
UNION ALL SELECT 'rows', 'AT_PROBLEMS',                     COUNT(*)::STRING FROM AT_PROBLEMS
UNION ALL SELECT 'rows', 'AT_DIAGNOSES',                    COUNT(*)::STRING FROM AT_DIAGNOSES
UNION ALL SELECT 'rows', 'AT_PRESCRIPTIONS',                COUNT(*)::STRING FROM AT_PRESCRIPTIONS
UNION ALL SELECT 'rows', 'AT_ADMINISTRATIONS',              COUNT(*)::STRING FROM AT_ADMINISTRATIONS
UNION ALL SELECT 'rows', 'AT_ABX_ADMINISTRATIONS',          COUNT(*)::STRING FROM AT_ABX_ADMINISTRATIONS
UNION ALL SELECT 'rows', 'AT_SURGERY_ABX',                  COUNT(*)::STRING FROM AT_SURGERY_ABX
UNION ALL SELECT 'rows', 'AT_TEMPERATURE',                  COUNT(*)::STRING FROM AT_TEMPERATURE
UNION ALL SELECT 'rows', 'AT_PATHOLOGY',                    COUNT(*)::STRING FROM AT_PATHOLOGY
UNION ALL SELECT 'rows', 'AT_CRP',                          COUNT(*)::STRING FROM AT_CRP
UNION ALL SELECT 'rows', 'AT_PRIOR_ADMISSIONS',             COUNT(*)::STRING FROM AT_PRIOR_ADMISSIONS
UNION ALL SELECT 'rows', 'AT_PRIOR_EMERGENCY',              COUNT(*)::STRING FROM AT_PRIOR_EMERGENCY
UNION ALL SELECT 'rows', 'AT_PRIOR_OUTPATIENT',             COUNT(*)::STRING FROM AT_PRIOR_OUTPATIENT
UNION ALL SELECT 'rows', 'AT_PROCEDURES_OPCS',              COUNT(*)::STRING FROM AT_PROCEDURES_OPCS;
