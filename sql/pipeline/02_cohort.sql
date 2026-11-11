-- =============================================================================
-- 02_cohort.sql   (ports step 1 of legacy get_surgery_cohort.sql)
-- PURPOSE   Flowchart steps 1-2: adult elective admissions with >=1 surgery in
--           the study window, then exclusion of emergency procedures.
-- READS     STG_SURGERY, STG_EPISODES
-- WRITES    AT_SURGERY_LINKED          grain: one row per (subject, spell,
--                                      surgery start, surgery stop, procedure).
--                                      Step 1 population, emergency flag kept.
--           AT_ELECTIVE_SURGERY_COHORT same grain, emergency events removed
--                                      (step 2 population).
--           AT_ELECTIVE_SPELLS         grain: one row per (subject, spell).
-- CHANGES vs legacy
--   * Surgery is linked to the consultant episode that CONTAINS it
--     (episode_contains_surgery = 1). The old join matched every episode in the
--     spell and duplicated surgeries. Preference order: surgery timestamp inside
--     the episode's start/end timestamps, then surgery date inside the episode's
--     dates, then lowest episode order. Unresolved cases are counted in AT_QA_CHECKS.
--   * Window bounds come from AT_PARAMS (00_params.sql: study_start, study_end, anchor)
--     and use < study_end + 1 day, so the whole end date is included.
--   * Dates are typed in 01_staging.sql, not compared as strings.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

CREATE OR REPLACE TABLE AT_SURGERY_LINKED AS
WITH surgeries AS (
    SELECT
        s.*,
        IFF(p.surgery_anchor = 'start', s.surgery_start_ts, s.surgery_stop_ts) AS surgery_anchor_ts
    FROM STG_SURGERY s
    CROSS JOIN AT_PARAMS p
),

candidates AS (
    SELECT
        s.SUBJECT,
        s.PROCEDURE_DESC,
        s.surgery_start_ts,
        s.surgery_stop_ts,
        s.surgery_anchor_ts,
        s.SURGICAL_AREA,

        e.SPELL_IDENTIFIER,
        e.EPISODE_IDENTIFIER,
        e.order_no_of_episode_n,
        e.ADMISSION_METHOD,
        e.ADMISSION_METHOD_DESC,
        e.DISCHARGE_DESTINATION,
        e.DISCHARGE_DESTINATION_DESC,
        e.DISCHARGE_METHOD,
        e.DISCHARGE_METHOD_DESC,
        e.INDEX_OF_MULTIPLE_DEPRIVATION_DECILE,
        e.MAIN_SPECIALTY_CODE,
        e.TREATMENT_FUNCTION_CODE,
        e.TREATMENT_FUNCTION_CODE_DES,
        e.admission_d,
        e.discharge_d,
        e.admission_ts,
        e.discharge_ts,
        e.episode_start_d,
        e.episode_end_d,
        e.episode_start_ts,
        e.episode_end_ts,
        e.age_at_admission_n,

        -- 1 if the surgery date falls inside this consultant episode
        IFF(TO_DATE(s.surgery_anchor_ts) BETWEEN e.episode_start_d AND e.episode_end_d, 1, 0)
            AS episode_contains_surgery,

        -- 1 if the surgery TIMESTAMP falls inside this episode's start/end timestamps
        -- (0 also when the episode has no usable times; the date match above is then the fallback)
        IFF(s.surgery_anchor_ts >= e.episode_start_ts AND s.surgery_anchor_ts < e.episode_end_ts, 1, 0)
            AS episode_contains_surgery_time,

        SUM(IFF(s.surgery_anchor_ts >= e.episode_start_ts AND s.surgery_anchor_ts < e.episode_end_ts, 1, 0)) OVER (
            PARTITION BY s.SUBJECT, e.SPELL_IDENTIFIER, s.surgery_start_ts, s.surgery_stop_ts, s.PROCEDURE_DESC
        ) AS n_episodes_containing_surgery_time,

        -- how many of the spell's episodes contain the surgery DATE (>1 = ambiguous before the time tie-break)
        SUM(IFF(TO_DATE(s.surgery_anchor_ts) BETWEEN e.episode_start_d AND e.episode_end_d, 1, 0)) OVER (
            PARTITION BY s.SUBJECT, e.SPELL_IDENTIFIER, s.surgery_start_ts, s.surgery_stop_ts, s.PROCEDURE_DESC
        ) AS n_episodes_containing_surgery,

        -- 1 on every row of a surgery event if ANY of its procedures is labelled emergency
        MAX(IFF(LOWER(s.PROCEDURE_DESC) LIKE '%emergency%', 1, 0)) OVER (
            PARTITION BY s.SUBJECT, s.surgery_start_ts, s.surgery_stop_ts
        ) AS has_emergency_flag

    FROM surgeries s
    CROSS JOIN AT_PARAMS p
    INNER JOIN STG_EPISODES e
        ON  s.SUBJECT = e.SUBJECT
        AND s.surgery_anchor_ts >= e.admission_d
        AND s.surgery_anchor_ts <  DATEADD(day, 1, e.discharge_d)

    WHERE e.discharge_d IS NOT NULL                       -- legacy: DISCHARGE_DATE != 'none'
      AND e.ADMISSION_METHOD IN ('11', '12', '13')
      AND e.age_at_admission_n >= p.min_age
      AND s.surgery_anchor_ts >= p.study_start
      AND s.surgery_anchor_ts <  DATEADD(day, 1, p.study_end)
)

SELECT
    *,
    HASH(SUBJECT, SPELL_IDENTIFIER, surgery_start_ts, surgery_stop_ts) AS surgery_event_id -- create a unique ID for each surgery event (subject, spell, start, stop)
FROM candidates
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY SUBJECT, SPELL_IDENTIFIER, surgery_start_ts, surgery_stop_ts, PROCEDURE_DESC
    ORDER BY episode_contains_surgery_time DESC, episode_contains_surgery DESC, order_no_of_episode_n ASC NULLS LAST, EPISODE_IDENTIFIER
) = 1;


CREATE OR REPLACE TABLE AT_ELECTIVE_SURGERY_COHORT AS
SELECT *
FROM AT_SURGERY_LINKED
WHERE has_emergency_flag = 0;


-- One row per spell. Spell-level fields are identical across a spell's rows
-- (checked in 09_qa.sql), so ANY_VALUE is safe.
CREATE OR REPLACE TABLE AT_ELECTIVE_SPELLS AS
SELECT
    SUBJECT,
    SPELL_IDENTIFIER,
    MIN(admission_d)  AS admission_d,
    MIN(discharge_d)  AS discharge_d,
    MIN(admission_ts) AS admission_ts,
    MIN(discharge_ts) AS discharge_ts,
    ANY_VALUE(ADMISSION_METHOD)                       AS ADMISSION_METHOD,
    ANY_VALUE(ADMISSION_METHOD_DESC)                  AS ADMISSION_METHOD_DESC,
    ANY_VALUE(age_at_admission_n)                     AS age_at_admission_n,
    ANY_VALUE(INDEX_OF_MULTIPLE_DEPRIVATION_DECILE)   AS INDEX_OF_MULTIPLE_DEPRIVATION_DECILE,
    ANY_VALUE(DISCHARGE_METHOD)                       AS DISCHARGE_METHOD,
    ANY_VALUE(DISCHARGE_METHOD_DESC)                  AS DISCHARGE_METHOD_DESC,
    ANY_VALUE(DISCHARGE_DESTINATION)                  AS DISCHARGE_DESTINATION,
    ANY_VALUE(DISCHARGE_DESTINATION_DESC)             AS DISCHARGE_DESTINATION_DESC,
    COUNT(DISTINCT surgery_event_id)                  AS n_surgery_events
FROM AT_ELECTIVE_SURGERY_COHORT
GROUP BY SUBJECT, SPELL_IDENTIFIER;


-- CHECK: funnel must shrink: linked (step 1) >= elective cohort (step 2) >= spells.
SELECT
    (SELECT COUNT(*) FROM AT_SURGERY_LINKED)           AS n_surgery_linked,
    (SELECT COUNT(*) FROM AT_ELECTIVE_SURGERY_COHORT)  AS n_elective_cohort,
    (SELECT COUNT(*) FROM AT_ELECTIVE_SPELLS)          AS n_elective_spells,
    IFF(n_elective_spells > 0
        AND n_elective_spells <= n_elective_cohort
        AND n_elective_cohort <= n_surgery_linked, 'OK', 'CHECK') AS check_result;
