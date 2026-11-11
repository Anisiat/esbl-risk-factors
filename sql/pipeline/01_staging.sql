-- =============================================================================
-- 01_staging.sql
-- PURPOSE   Make dates trustworthy ONCE. Staging tables are copies of the raw
--           iCARE tables with extra typed columns (suffix _ts = timestamp,
--           _d = date, _n = number). Every later file uses these typed columns
--           and never parses a raw date again.
--           Unparseable values ('none', blanks, odd formats) become NULL and
--           are counted in AT_QA_CHECKS (09_qa.sql).
-- READS     ICARE_SURGERY_ANON, ICARE_EPISODES_ANON
-- WRITES    STG_SURGERY   grain: one row per procedure row (as in the raw table)
--           STG_EPISODES  grain: one row per consultant episode (several per
--                         spell), only for patients who appear in the surgery table
-- NOTE      TRY_TO_TIMESTAMP(x::STRING) uses Snowflake's AUTO format detection.
--           If the QA check reports many unparseable values, the format needs
--           to be added explicitly here (one place to fix).
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

CREATE OR REPLACE TABLE STG_SURGERY AS
SELECT
    s.SUBJECT,
    s.PROCEDURE_DESC,
    s.SURGERY_START_DT,
    s.SURGERY_STOP_DT,
    s.SURGICAL_AREA,
    TRY_TO_TIMESTAMP(s.SURGERY_START_DT::STRING) AS surgery_start_ts,
    TRY_TO_TIMESTAMP(s.SURGERY_STOP_DT::STRING)  AS surgery_stop_ts
FROM ICHT_PROD.ICARE_ICHT.ICARE_SURGERY_ANON s;


CREATE OR REPLACE TABLE STG_EPISODES AS
SELECT
    e.SUBJECT,
    e.SPELL_IDENTIFIER,
    e.EPISODE_IDENTIFIER,
    e.ADMISSION_DATE,
    e.ADMISSION_TIME,
    e.ADMISSION_METHOD,
    e.ADMISSION_METHOD_DESC,
    e.DISCHARGE_DATE,
    e.DISCHARGE_TIME,
    e.DISCHARGE_DESTINATION,
    e.DISCHARGE_DESTINATION_DESC,
    e.DISCHARGE_METHOD,
    e.DISCHARGE_METHOD_DESC,
    e.INDEX_OF_MULTIPLE_DEPRIVATION_DECILE,
    e.MAIN_SPECIALTY_CODE,
    e.TREATMENT_FUNCTION_CODE,
    e.TREATMENT_FUNCTION_CODE_DES,          -- truncated name is the real column (docs / CLAUDE.md)
    TO_DATE(TRY_TO_TIMESTAMP(e.ADMISSION_DATE::STRING))     AS admission_d,
    TO_DATE(TRY_TO_TIMESTAMP(e.DISCHARGE_DATE::STRING))     AS discharge_d,
    TO_DATE(TRY_TO_TIMESTAMP(e.EPISODE_START_DATE::STRING)) AS episode_start_d,
    TO_DATE(TRY_TO_TIMESTAMP(e.EPISODE_END_DATE::STRING))   AS episode_end_d,
    -- date and time are separate text columns ('2022-02-01' and '13:05:00'); NULL if either is missing/'none'
    TRY_TO_TIMESTAMP(e.ADMISSION_DATE::STRING     || ' ' || e.ADMISSION_TIME::STRING)     AS admission_ts,
    TRY_TO_TIMESTAMP(e.DISCHARGE_DATE::STRING     || ' ' || e.DISCHARGE_TIME::STRING)     AS discharge_ts,
    TRY_TO_TIMESTAMP(e.EPISODE_START_DATE::STRING || ' ' || e.EPISODE_START_TIME::STRING) AS episode_start_ts,
    TRY_TO_TIMESTAMP(e.EPISODE_END_DATE::STRING   || ' ' || e.EPISODE_END_TIME::STRING)   AS episode_end_ts,
    TRY_TO_DOUBLE(e.AGE_AT_ADMISSION::STRING)               AS age_at_admission_n,
    TRY_TO_NUMBER(e.ORDER_NO_OF_EPISODE::STRING)            AS order_no_of_episode_n
FROM ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_ANON e
WHERE e.SUBJECT IN (SELECT DISTINCT SUBJECT FROM STG_SURGERY);


-- CHECK: staging must not add or drop surgery rows, and episodes must not be empty.
SELECT
    (SELECT COUNT(*) FROM ICHT_PROD.ICARE_ICHT.ICARE_SURGERY_ANON) AS n_raw_surgery,
    (SELECT COUNT(*) FROM STG_SURGERY)                              AS n_stg_surgery,
    (SELECT COUNT(*) FROM STG_EPISODES)                             AS n_stg_episodes,
    IFF(n_raw_surgery = n_stg_surgery AND n_stg_episodes > 0, 'OK', 'CHECK') AS check_result;
