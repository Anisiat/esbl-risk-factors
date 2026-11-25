-- =============================================================================
-- 00_params.sql
-- PURPOSE   Single place for every study parameter used by the pipeline.
-- READS     nothing
-- WRITES    AT_PARAMS   grain: exactly one row. Later files read it with
--                       CROSS JOIN AT_PARAMS p and use p.study_end etc.
-- HOW TO RUN
--   Every file starts with its own USE DATABASE / USE SCHEMA, and parameters
--   live in a saved table (not session variables), so each file can be run on
--   its own in any worksheet. Run in number order: 00, 01, 02, 03, 04, 05, 09.
--   To run the whole pipeline in one go, paste the files into one worksheet
--   in that order.
--   Each of 01-05 ends with a row-count check (a single SELECT); run it last in
--   that file and read the result before moving on.
--   Re-run 00 (and everything after it) whenever a parameter changes.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

-- Create the parameters table
CREATE OR REPLACE TABLE AT_PARAMS AS
SELECT
    -- Study window. Surgery anchor must fall in [study_start, study_end] inclusive
    -- (implemented as anchor < study_end + 1 day).
    '2015-01-01'::DATE AS study_start,
    '2025-09-30'::DATE AS study_end,         -- decided 2026-11-13 (legacy SQL used 2025-08-31)

    -- Which surgery timestamp must fall inside admission -> discharge: 'start' or 'stop'.
    'start'            AS surgery_anchor,    -- decided 2026-11-13 (legacy SQL used stop)

    18                 AS min_age,
    90                 AS lookback_days,     -- prior-exposure window before index admission
    30                 AS max_days_after_surgery,  -- samples are linked up to this many days after a spell's last surgery
                                                   -- (same value as config.yaml: sample_eligibility.max_days_after_surgery)

    -- Perioperative antibiotic window around each surgery (decided 2026-11-18):
    -- from prophylaxis_hours_before_start before surgery start to prophylaxis_hours_after_stop after surgery stop.
    2                  AS prophylaxis_hours_before_start,
    1                  AS prophylaxis_hours_after_stop,

    -- Short commit hash of the code that produced the tables. Do not edit: the
    -- placeholder is replaced by scripts/make_bundle.py in the uploaded copy.
    '{{GIT_HASH}}'     AS git_hash;
