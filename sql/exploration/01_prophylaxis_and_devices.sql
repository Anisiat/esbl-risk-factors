-- =============================================================================
-- 01_prophylaxis_and_devices.sql
-- PURPOSE   One-off exploration to run in the SDE BEFORE writing the antibiotic,
--           prophylaxis and device code, so drug names, routes and codes are
--           classified from the real data instead of guessed.
-- HOW       Reads the RAW iCARE tables only, so it does not need the new pipeline
--           (sql/pipeline) to have been run. It therefore covers all patients in
--           iCARE, not just the study cohort: fine for classifying names, routes
--           and codes, but the counts are not cohort counts.
--           Run EACH statement on its own. Every list drops groups with fewer than
--           10 patients (small-number rule). Nothing here is exported to data/raw/.
-- NOTE      Snowflake needs every non-aggregated SELECT column in GROUP BY.
-- =============================================================================


-- 1. Anti-infective names in the administrations table ---------------------------------------
SELECT LOWER(TRIM(a.MEDICATION_NAME_SHORT)) AS med,
       COUNT(*)                             AS n_admin,
       COUNT(DISTINCT a.SUBJECT)            AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
WHERE LOWER(a.THERAPEUTICAL_CLASS) = 'anti-infectives'
GROUP BY 1
HAVING COUNT(DISTINCT a.SUBJECT) >= 10
ORDER BY n_patients DESC;


-- 2. Anti-infective names that are NOT in the antibiotic whitelist ABX_WL (gaps to classify) --
-- (ABX_WL is your existing table in ICHT_SANDBOX_PROD.SMTPATH_25046; needs a MEDICATION_NAME_SHORT column.)
SELECT LOWER(TRIM(a.MEDICATION_NAME_SHORT)) AS med,
       COUNT(*)                             AS n_admin,
       COUNT(DISTINCT a.SUBJECT)            AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
WHERE LOWER(a.THERAPEUTICAL_CLASS) = 'anti-infectives'
  AND NOT EXISTS (
      SELECT 1 FROM ICHT_SANDBOX_PROD.SMTPATH_25046.ABX_WL w
      WHERE LOWER(TRIM(w.MEDICATION_NAME_SHORT)) = LOWER(TRIM(a.MEDICATION_NAME_SHORT))
  )
GROUP BY 1
HAVING COUNT(DISTINCT a.SUBJECT) >= 10
ORDER BY n_patients DESC;


-- 3. Routes of anti-infectives ------------------------------------------------------------------
SELECT LOWER(TRIM(a.ROUTE))      AS route,
       COUNT(*)                  AS n_admin,
       COUNT(DISTINCT a.SUBJECT) AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
WHERE LOWER(a.THERAPEUTICAL_CLASS) = 'anti-infectives'
GROUP BY 1
HAVING COUNT(DISTINCT a.SUBJECT) >= 10
ORDER BY n_patients DESC;


-- 4. Administration status (are cancelled / not-given doses in the table?) -----------------------
SELECT LOWER(TRIM(a.ADMINISTRATION_STATUS)) AS status,
       COUNT(*)                             AS n_admin,
       COUNT(DISTINCT a.SUBJECT)            AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
WHERE LOWER(a.THERAPEUTICAL_CLASS) = 'anti-infectives'
GROUP BY 1
HAVING COUNT(DISTINCT a.SUBJECT) >= 10
ORDER BY n_patients DESC;


-- 5. Is surgery stop reliable? (prophylaxis window ends 1 h after surgery stop) ------------------
-- One row of counts: suppress any value below 10 before copying it out.
WITH s AS (
    SELECT TRY_TO_TIMESTAMP(SURGERY_START_DT::STRING) AS start_ts,
           TRY_TO_TIMESTAMP(SURGERY_STOP_DT::STRING)  AS stop_ts
    FROM ICHT_PROD.ICARE_ICHT.ICARE_SURGERY_ANON
)
SELECT COUNT(*)                                          AS n_surgeries,
       COUNT_IF(stop_ts IS NULL)                         AS n_no_stop,
       COUNT_IF(stop_ts <= start_ts)                     AS n_stop_not_after_start,
       COUNT_IF(DATEDIFF('hour', start_ts, stop_ts) > 12) AS n_over_12h
FROM s;


-- 6. Catheter / central line procedures coded as OPCS (procedures table) -------------------------
SELECT LOWER(TRIM(p.PROCEDURE_NAME_OPCS)) AS procedure_name,
       p.PROCEDURE_CODE_OPCS              AS opcs,
       COUNT(DISTINCT p.SUBJECT)          AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_PROCEDURES_ANON p
WHERE LOWER(p.PROCEDURE_NAME_OPCS) LIKE '%catheter%' OR LOWER(p.PROCEDURE_NAME_OPCS) LIKE '%central venous%'
GROUP BY 1, 2
HAVING COUNT(DISTINCT p.SUBJECT) >= 10
ORDER BY n_patients DESC;


-- 7. Device observations in the vital signs table (nursing documentation) ------------------------
SELECT LOWER(TRIM(v.OBSERVATION_NAME)) AS observation,
       COUNT(DISTINCT v.SUBJECT)       AS n_patients
FROM ICHT_PROD.ICARE_ICHT.ICARE_VITAL_SIGNS_ANON v
WHERE LOWER(v.OBSERVATION_NAME) LIKE '%catheter%'
   OR LOWER(v.OBSERVATION_NAME) LIKE '%line%'
   OR LOWER(v.OBSERVATION_NAME) LIKE '%device%'
GROUP BY 1
HAVING COUNT(DISTINCT v.SUBJECT) >= 10
ORDER BY n_patients DESC;
