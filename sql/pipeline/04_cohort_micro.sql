-- =============================================================================
-- 04_cohort_micro.sql   (ports step 2 of legacy get_surgery_cohort.sql)
-- PURPOSE   Link microbiology to elective spells (flowchart step 3: spells with
--           >=1 culture of an eligible organism during the admission or up to
--           max_days_after_surgery (00_params.sql) after the spell's last surgery).
-- READS     AT_ELECTIVE_SPELLS, AT_MICROBIOLOGY_EKP
-- WRITES    AT_ELECTIVE_SPELL_MICROBIOLOGY
--             grain: one row per (spell, lab_test_id, organism_bug).
--           AT_INFECTION_SPELLS
--             grain: one row per (subject, spell) with >=1 such culture.
--             Every aux table in 05 is restricted to these spells.
-- CHANGES vs legacy
--   * Linked on subject AND collection time within [admission, discharge], at
--     SPELL level. Legacy joined on subject alone and attached every surgery
--     row to every culture a patient ever had, multiplying rows.
--   * Surgery detail is NOT repeated here. Python joins this table to
--     AT_ELECTIVE_SURGERY_COHORT on (subject, spell_identifier) and attributes
--     each sample to a surgery (analysis_plan TODO: most recent preceding).
--   * The >=48 h after admission and post-surgery filters remain in Python.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

-- A sample is linked to a spell if it was collected from admission until the LATER of
-- discharge and (last surgery in the spell + max_days_after_surgery). So samples taken
-- after discharge (community, A&E, outpatient or a readmission) are included when they
-- fall within the post-surgery window. This is a deliberately wide net: Python applies
-- the exact per-sample rules (postoperative, >=48 h after admission, <= 30 days after the
-- most recent preceding surgery). The microbiology table has no location column, so
-- post-discharge samples cannot be split by setting; collected_during_admission marks
-- the in-hospital ones.
-- If a sample falls in the window of two spells, it is kept once, on the spell that
-- contains it, otherwise on the most recent admission.
CREATE OR REPLACE TABLE AT_ELECTIVE_SPELL_MICROBIOLOGY AS
WITH last_surgery AS (
    SELECT SUBJECT, SPELL_IDENTIFIER, MAX(surgery_anchor_ts) AS last_surgery_anchor_ts
    FROM AT_ELECTIVE_SURGERY_COHORT
    GROUP BY SUBJECT, SPELL_IDENTIFIER
)
SELECT
    m.*,
    s.* EXCLUDE (SUBJECT),
    l.last_surgery_anchor_ts,
    IFF(m.collect_ts < DATEADD(day, 1, s.discharge_d), 1, 0) AS collected_during_admission
FROM AT_ELECTIVE_SPELLS s
INNER JOIN last_surgery l
    ON  s.SUBJECT = l.SUBJECT
    AND s.SPELL_IDENTIFIER = l.SPELL_IDENTIFIER
CROSS JOIN AT_PARAMS p
INNER JOIN AT_MICROBIOLOGY_EKP m
    ON  s.SUBJECT = m.SUBJECT
    AND m.collect_ts >= s.admission_d
    AND m.collect_ts <  GREATEST(
            DATEADD(day, 1, s.discharge_d)::TIMESTAMP_NTZ,     -- cast so both arguments are timestamps
            DATEADD(day, p.max_days_after_surgery, l.last_surgery_anchor_ts)::TIMESTAMP_NTZ
        )
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY m.LAB_TEST_ID, m.ORGANISM_BUG
    ORDER BY IFF(m.collect_ts < DATEADD(day, 1, s.discharge_d), 1, 0) DESC, s.admission_d DESC
) = 1;


CREATE OR REPLACE TABLE AT_INFECTION_SPELLS AS
SELECT s.*
FROM AT_ELECTIVE_SPELLS s
WHERE EXISTS (
    SELECT 1
    FROM AT_ELECTIVE_SPELL_MICROBIOLOGY m
    WHERE m.SUBJECT = s.SUBJECT
      AND m.SPELL_IDENTIFIER = s.SPELL_IDENTIFIER
);


-- CHECK: infection spells are a non-empty subset of elective spells, one row per spell.
SELECT
    (SELECT COUNT(*) FROM AT_ELECTIVE_SPELLS)                                   AS n_elective_spells,
    (SELECT COUNT(*) FROM AT_INFECTION_SPELLS)                                  AS n_infection_spells,
    (SELECT COUNT(DISTINCT SUBJECT, SPELL_IDENTIFIER) FROM AT_INFECTION_SPELLS) AS n_distinct_infection_spells,
    (SELECT COUNT(*) FROM AT_ELECTIVE_SPELL_MICROBIOLOGY)                       AS n_spell_micro_rows,
    IFF(n_infection_spells > 0
        AND n_infection_spells <= n_elective_spells
        AND n_infection_spells = n_distinct_infection_spells, 'OK', 'CHECK')    AS check_result;
